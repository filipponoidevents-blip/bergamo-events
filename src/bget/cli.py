"""Command line entry point.

    python -m bget collect            # run all enabled sources, merge into data/events.json
    python -m bget calendar           # write out/bergamo.ics and out/alerts.ics
    python -m bget alerts             # print new alerts (and write out/alerts.md)
    python -m bget carousel --week 2026-11-16
    python -m bget carousel --month 2026-11
    python -m bget carousel --midweek 2026-11-18   # pre-party + non-music, Thursday to Sunday
    python -m bget posts [--date 2026-11-23]        # whatever the posting cadence says is due that day
    python -m bget run                # collect + calendar + alerts (what the scheduler calls)
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone

from . import calendar_ics, carousel, social
from .config import Config
from . import fascia
from .genres import GenreAssigner
from .headliners import HeadlinerFinder
from .models import Scope
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
            write_other_events(cfg.root, other_events(cfg, q["items"], ZoneInfo(cfg.settings.get("timezone", "Europe/Rome"))))
    # rescore everything so config edits apply to stored events too
    genre = GenreAssigner(cfg)
    heads = HeadlinerFinder(cfg)
    for ev in store.all():
        ev.genre, ev.genre_source = genre.assign(ev)
        ev.headliners = heads.find(ev)
        ev.fascia_auto, ev.fascia_why = fascia.classify(ev, cfg)
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
    (out / "bergamo.ics").write_text(calendar_ics.build_ics(upcoming(store, Scope.LOCAL), "Bergamo Nights"))
    fascia.write_report(cfg.root, [e for e in upcoming(store, Scope.LOCAL, days_back=0)])
    (out / "alerts.ics").write_text(calendar_ics.build_ics(
        upcoming(store, Scope.ALERT), "Bergamo Nights · Big events", prefix="⚡ ", reminders=maintenance_reminders(cfg)))
    # one combined feed is handy for a single subscription
    both = sorted(upcoming(store, Scope.LOCAL) + upcoming(store, Scope.ALERT), key=lambda e: e.start)
    (out / "all.ics").write_text(calendar_ics.build_ics(both, "Bergamo Nights · All"))


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


def _other_events(cfg: Config):
    from zoneinfo import ZoneInfo
    q = InstagramSource({"id": "ig", "accounts": []}, cfg)._load_review()
    return other_events(cfg, q["items"], ZoneInfo(cfg.settings.get("timezone", "Europe/Rome")))


def make_carousel(cfg: Config, store: Store, week: str | None, month: str | None,
                  midweek: str | None = None, formats: tuple[str, ...] = ("ig", "tt")) -> list:
    """Render a post for Instagram (4:5) and TikTok (9:16) and write its post bundle."""
    def serata(e) -> bool:   # fascia.py: times, post clues, price, venue
        return not cfg.is_preserata(e)
    local = [e for e in store.all() if e.scope == Scope.LOCAL]
    # serate are split into pages by genre group; preserata nights follow on white pages (Filippo)
    genres = cfg.settings.get("post_genres")
    pre = lambda e: not serata(e)  # noqa: E731
    alerts = [e for e in store.all() if e.scope == Scope.ALERT and serata(e)]
    outdir = cfg.root / "out" / "carousels"
    if week or midweek:
        d = date.fromisoformat(week or midweek)
        monday = d - timedelta(days=d.weekday())
    if week:
        build = lambda fmt: carousel.weekly(local, monday, alerts, fmt, preserata=pre, genres=genres)  # noqa: E731
        stem, title = f"week_{monday.isoformat()}", f"Serate Bergamo · settimana {monday:%d/%m}"
    elif midweek:
        others = _other_events(cfg)
        build = lambda fmt: carousel.midweek(others, monday, fmt)  # noqa: E731
        stem, title = f"midweek_{monday.isoformat()}", f"Prima della serata · {monday + timedelta(days=3):%d/%m}"
    else:
        y, m = map(int, month.split("-"))
        build = lambda fmt: carousel.monthly(local, y, m, alerts, fmt, preserata=pre, genres=genres)  # noqa: E731
        stem, title = f"month_{y}-{m:02d}", f"Serate Bergamo · {carousel.MESI[m]} {y}"
    paths, slides = {}, None
    for fmt in formats:
        slides = build(fmt)
        paths[fmt] = carousel.save(slides, outdir, f"{stem}_{fmt}")
    bundle = social.build_bundle(paths.get("ig", []), [evs for _, evs in slides], title,
                                 outdir / f"{stem}_post.json", tiktok_paths=paths.get("tt"))
    print(f"{len(slides)} slides x {len(paths)} formats -> {outdir}  (post bundle: {bundle.name})")
    return [p for ps in paths.values() for p in ps]


def due_posts(today: date) -> list[tuple[str, str]]:
    """Posting cadence (Filippo, 2026-11-17): weekly every Monday (this week), midweek every
    Wednesday (Thursday to Sunday), monthly on the last Sunday of the month (next month)."""
    out = []
    if today.weekday() == 0:
        out.append(("week", today.isoformat()))
    if today.weekday() == 2:
        out.append(("midweek", today.isoformat()))
    if today.weekday() == 6 and (today + timedelta(days=7)).month != today.month:
        nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append(("month", f"{nxt.year}-{nxt.month:02d}"))
    return out


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
    g.add_argument("--midweek", help="any date in the week, YYYY-MM-DD")
    c.add_argument("--format", choices=["ig", "tt", "both"], default="both")
    ps = sub.add_parser("posts", help="render the posts that are due today (weekly Mon, midweek Wed, monthly last Sun)")
    ps.add_argument("--date", help="pretend today is this date, YYYY-MM-DD")
    a = p.parse_args(argv)

    cfg = Config.load()
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
        fmts = ("ig", "tt") if a.format == "both" else (a.format,)
        make_carousel(cfg, store, a.week, a.month, a.midweek, fmts)
    if a.cmd == "posts":
        today = date.fromisoformat(a.date) if a.date else datetime.now(timezone.utc).date()
        due = due_posts(today)
        if not due:
            print(f"nothing due on {today}")
        for kind, arg in due:
            make_carousel(cfg, store, **{"week": None, "month": None, "midweek": None, kind: arg})


if __name__ == "__main__":
    main()
