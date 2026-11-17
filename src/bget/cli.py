"""Command line entry point.

    python -m bget collect            # run all enabled sources, merge into data/events.json
    python -m bget calendar           # write out/bergamo.ics and out/alerts.ics
    python -m bget alerts             # print new alerts (and write out/alerts.md)
    python -m bget carousel --week 2026-11-16
    python -m bget carousel --month 2026-11
    python -m bget run                # collect + calendar + alerts (what the scheduler calls)
    python -m bget --city brescia run # same for another city (folder cities/brescia/)
    python -m bget --city all run     # every city, one after the other
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone

from . import calendar_ics, carousel, social
from .config import Config, all_cities, city_root
from . import fascia
from .genres import GenreAssigner
from .headliners import HeadlinerFinder
from .models import Scope
from .names import name_map
from .ranking import Ranker
from .relevance import score_event
from .sources import REGISTRY
from .sources.instagram import InstagramSource, other_events, write_other_events, write_review_md
from .store import Store

log = logging.getLogger("bget")


def collect(cfg: Config, store: Store) -> dict[str, int]:
    stats = {"new": 0, "updated": 0, "errors": 0}
    for spec in cfg.sources:
        if not spec.get("enabled"):
            continue
        cls = REGISTRY.get(spec["type"])
        if not cls:
            log.error("unknown source type %s", spec["type"])
            continue
        src = cls(spec, cfg)
        try:
            for ev in src.collect():
                if not ev.title or not ev.start:
                    continue
                cfg.enrich_venue(ev.venue)
                stats[store.upsert(ev)] += 1
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            log.error("source %s failed: %s", spec["id"], e)
        if isinstance(src, InstagramSource):
            flag = cfg.root / "out" / "token_problem.txt"
            flag.parent.mkdir(exist_ok=True)
            if src.token_problem:
                flag.write_text(src.token_problem + "\n\n" + TOKEN_HELP)
            elif flag.exists():
                flag.unlink()
            stats["removed"] = stats.get("removed", 0) + store.drop_sources(src.rejected_source_ids())
            q = src._load_review()
            write_review_md(cfg.root, q["items"], *src.decisions(), unreadable=q.get("unreadable_accounts"))
            from zoneinfo import ZoneInfo
            write_other_events(cfg.root, other_events(cfg, q["items"], ZoneInfo(cfg.settings.get("timezone", "Europe/Rome"))),
                               cfg.label("brand"), cfg.label("feed_other"))
    hidden = cfg.root / "config" / "hidden.yaml"
    if hidden.exists():
        import yaml
        stats["removed"] = stats.get("removed", 0) + store.hide((yaml.safe_load(hidden.read_text()) or {}).get("hide") or [])
    # rescore everything so config edits apply to stored events too
    genre = GenreAssigner(cfg)
    heads = HeadlinerFinder(cfg)
    names = name_map(cfg)
    ranker = Ranker(cfg)
    for ev in store.all():
        for a in ev.artists:  # profile names instead of handles (Filippo)
            a.name = names.get((a.socials.instagram or a.name).lstrip("@").lower(), a.name)
        ev.genre, ev.genre_source = genre.assign(ev)
        ev.headliners = heads.find(ev)
        ev.fascia_auto, ev.fascia_why = fascia.classify(ev, cfg)
        ev.rank = ranker.rank(ev)
        ev.in_posts = not any(v["name"] == ev.venue.name and v.get("in_posts") is False for v in cfg.venues)
        score_event(ev, cfg)
    return stats


def upcoming(store: Store, scope: Scope, days_back: int = 7):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    return [e for e in store.all() if e.scope == scope and e.start >= cutoff]


TOKEN_HELP = ("Instagram token expires soon. Graph API Explorer -> Generate Access Token (permissions: "
              "instagram_basic, instagram_manage_insights, pages_read_engagement, pages_show_list, "
              "business_management) -> Access Token Tool -> Extend -> paste into the IG_ACCESS_TOKEN "
              "GitHub secret, then update instagram.token_renewed in config/settings.yaml.")


def maintenance_reminders(cfg: Config) -> list[tuple]:
    """All-day notes in the alerts calendar: renew the Instagram token before it expires (~60 days)."""
    out = []
    ig = cfg.settings.get("instagram", {})
    if ig.get("token_renewed"):
        renewed = date.fromisoformat(str(ig["token_renewed"]))
        for d in (50, 55, 58):
            out.append((f"ig-token-{renewed}-{d}", renewed + timedelta(days=d),
                        "⚠️ Rinnova il token Instagram", TOKEN_HELP))
    flag = cfg.root / "out" / "token_problem.txt"
    if flag.exists():
        out.append(("ig-token-broken", date.today(), "⚠️ Token Instagram non valido", flag.read_text()))
    return out


def write_calendars(cfg: Config, store: Store) -> None:
    out = cfg.root / "out"
    out.mkdir(exist_ok=True)
    brand = cfg.label("brand")
    (out / cfg.label("feed_local")).write_text(calendar_ics.build_ics(upcoming(store, Scope.LOCAL), brand))
    fascia.write_report(cfg.root, [e for e in upcoming(store, Scope.LOCAL, days_back=0)])
    (out / cfg.label("feed_alerts")).write_text(calendar_ics.build_ics(
        upcoming(store, Scope.ALERT), f"{brand} · Big events", prefix="⚡ ", reminders=maintenance_reminders(cfg),
        home=cfg.label("name")))
    # one combined feed is handy for a single subscription
    both = sorted(upcoming(store, Scope.LOCAL) + upcoming(store, Scope.ALERT), key=lambda e: e.start)
    (out / cfg.label("feed_all")).write_text(calendar_ics.build_ics(both, f"{brand} · All", home=cfg.label("name")))


def write_alerts(cfg: Config, store: Store) -> str:
    alerts = [e for e in store.all() if e.alert_level and e.start >= datetime.now(timezone.utc)]
    lines = ["# Big event alerts", ""]
    for e in alerts:
        lines.append(
            f"- **{e.title}** ({e.alert_level}) {e.start:%a %d %b %H:%M} at {e.venue.name}, "
            f"{e.venue.city} ({e.venue.province or '?'}, {e.distance_km} km). "
            f"Why: {', '.join(e.reasons)}. {e.url or ''}".rstrip()
        )
    text = "\n".join(lines) + "\n"
    (cfg.root / "out" / "alerts.md").write_text(text)
    return text


def make_carousel(cfg: Config, store: Store, week: str | None, month: str | None) -> None:
    local = [e for e in store.all() if e.scope == Scope.LOCAL]
    alerts = [e for e in store.all() if e.scope == Scope.ALERT]
    outdir = cfg.root / "out" / "carousels"
    if week:
        d = date.fromisoformat(week)
        monday = d - timedelta(days=d.weekday())
        slides = carousel.weekly(local, monday, alerts)
        stem, title = f"week_{monday.isoformat()}", f"{cfg.label('brand')} · settimana {monday:%d/%m}"
    else:
        y, m = map(int, month.split("-"))
        slides = carousel.monthly(local, y, m, alerts)
        stem, title = f"month_{y}-{m:02d}", f"{cfg.label('brand')} · {carousel.MESI[m]} {y}"
    paths = carousel.save(slides, outdir, stem)
    bundle = social.build_bundle(paths, [evs for _, evs in slides], title, outdir / f"{stem}_post.json")
    print(f"{len(paths)} slides -> {outdir}  (post bundle: {bundle.name})")


def apply_city_labels(cfg: Config) -> None:
    """Point the post renderer and captions at this city's labels (account, area line, hashtags)."""
    for name, value in (("HANDLE", cfg.label("handle")), ("AREA", cfg.label("area"))):
        if hasattr(carousel, name):
            setattr(carousel, name, value)
    if isinstance(getattr(carousel, "THEME", None), dict) and "handle" in carousel.THEME:
        carousel.THEME["handle"] = cfg.label("handle")
    social.BASE_TAGS[:] = cfg.hashtags()


def probe(cfg: Config, urls: list[str], follow: str | None) -> None:
    """Diagnostics for adding a new site: run it where the site is reachable (e.g. in Actions)."""
    import re
    from collections import Counter
    from urllib.parse import urlparse

    from .sources.jsonld import JsonLdSource

    src = JsonLdSource({"id": "probe", "urls": urls, "follow": follow, "max_pages": 5}, cfg)
    for url in urls:
        print(f"\n=== {url}")
        try:
            html = src.fetch(url)
        except Exception as e:  # noqa: BLE001
            print(f"  fetch failed: {e}")
            continue
        if html.lstrip()[:1] in "[{":
            print(f"  JSON, {len(html)} bytes: {html[:1500]}")
            continue
        print(f"  {len(html)} bytes, {html.count('application/ld+json')} JSON-LD blocks, "
              f"__NEXT_DATA__={'yes' if '__NEXT_DATA__' in html else 'no'}")
        api = sorted(set(re.findall(r"https?://[a-z0-9.-]+/(?:v\d|api)[^\"'\\\s<>]{0,60}", html)))[:15]
        if api:
            print("  API-looking URLs in the page:", *api, sep="\n    ")
        for key in ("__next_f", "startingTime", "startDate", "starting_time", "eventId", "\"events\""):
            i = html.find(key)
            if i >= 0:
                print(f"  '{key}' x{html.count(key)}: ...{html[max(0, i - 120):i + 200]!r}...")
        evs = list(src.parse(html, url))
        print(f"  events on the page itself: {len(evs)}")
        for e in evs[:3]:
            print(f"   - {e.start:%Y-%m-%d %H:%M} {e.title} @ {e.venue.name}, {e.venue.city}")
        host = urlparse(url).netloc
        links = JsonLdSource.links(html, url, re.compile(re.escape(host)))
        shapes = Counter(re.sub(r"[0-9]+", "N", urlparse(u).path).rsplit("/", 1)[0] for u in links)
        print("  most common internal link shapes:")
        for shape, n in shapes.most_common(8):
            print(f"    {n:3d}  {shape}/...")
        if follow:
            fl = JsonLdSource.links(html, url, re.compile(follow))
            print(f"  links matching --follow: {len(fl)} (first: {fl[:2]})")
            for link in fl[:3]:
                try:
                    found = list(src.parse(src.fetch(link), link))
                except Exception as e:  # noqa: BLE001
                    print(f"   {link}: fetch failed: {e}")
                    continue
                print(f"   {link}: {len(found)} events" + (
                    f" e.g. {found[0].start:%Y-%m-%d} {found[0].title} @ {found[0].venue.name}" if found else ""))


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="bget")
    p.add_argument("--city", default="bergamo", help="bergamo (default), a folder name in cities/, or all")
    sub = p.add_subparsers(dest="cmd", required=True)
    for c in ("collect", "calendar", "alerts", "run"):
        sub.add_parser(c)
    pr = sub.add_parser("probe", help="check what a page offers: JSON-LD events and links")
    pr.add_argument("urls", nargs="+")
    pr.add_argument("--follow", help="regex for event links to open from a listing page")
    c = sub.add_parser("carousel")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--week", help="any date in the week, YYYY-MM-DD")
    g.add_argument("--month", help="YYYY-MM")
    a = p.parse_args(argv)
    if a.city == "all":
        failed = []
        for city in all_cities():
            print(f"=== {city}")
            try:   # one city's problem must never stop the others' calendars
                run_city(a, city)
            except Exception:  # noqa: BLE001
                log.exception("city %s failed", city)
                failed.append(city)
        if failed and "bergamo" in failed:
            raise SystemExit(f"failed: {', '.join(failed)}")
        return
    run_city(a, a.city)


def run_city(a, city: str) -> None:
    cfg = Config.load(city_root(city))
    apply_city_labels(cfg)
    if a.cmd == "probe":
        probe(cfg, a.urls, a.follow)
        return
    store = Store(cfg.root / "data" / "events.json")
    if a.cmd in ("collect", "run"):
        stats = collect(cfg, store)
        store.save()
        print(f"collected: {stats}")
    else:
        for ev in store.all():
            score_event(ev, cfg)
    if a.cmd in ("calendar", "run"):
        write_calendars(cfg, store)
        print("calendars written to out/")
    if a.cmd in ("alerts", "run"):
        print(write_alerts(cfg, store))
    if a.cmd == "carousel":
        make_carousel(cfg, store, a.week, a.month)


if __name__ == "__main__":
    main()
