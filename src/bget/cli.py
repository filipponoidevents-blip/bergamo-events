"""Command line entry point.

    python -m bget collect            # run all enabled sources, merge into data/events.json
    python -m bget calendar           # write out/bergamo.ics and out/alerts.ics
    python -m bget alerts             # print new alerts (and write out/alerts.md)
    python -m bget carousel --week 2026-11-16
    python -m bget carousel --month 2026-11
    python -m bget run                # collect + calendar + alerts (what the scheduler calls)
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone

from . import calendar_ics, carousel, social
from .config import Config
from .models import Scope
from .relevance import score_event
from .sources import REGISTRY
from .sources.instagram import InstagramSource, write_review_md
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
            stats["removed"] = stats.get("removed", 0) + store.drop_sources(src.rejected_source_ids())
            q = src._load_review()
            write_review_md(cfg.root, q["items"], *src.decisions())
    # rescore everything so config edits apply to stored events too
    for ev in store.all():
        score_event(ev, cfg)
    return stats


def upcoming(store: Store, scope: Scope, days_back: int = 7):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    return [e for e in store.all() if e.scope == scope and e.start >= cutoff]


def write_calendars(cfg: Config, store: Store) -> None:
    out = cfg.root / "out"
    out.mkdir(exist_ok=True)
    (out / "bergamo.ics").write_text(calendar_ics.build_ics(upcoming(store, Scope.LOCAL), "Bergamo Nights"))
    (out / "alerts.ics").write_text(
        calendar_ics.build_ics(upcoming(store, Scope.ALERT), "Bergamo Nights · Big events", prefix="⚡ "))
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


def make_carousel(cfg: Config, store: Store, week: str | None, month: str | None) -> None:
    local = [e for e in store.all() if e.scope == Scope.LOCAL]
    alerts = [e for e in store.all() if e.scope == Scope.ALERT]
    outdir = cfg.root / "out" / "carousels"
    if week:
        d = date.fromisoformat(week)
        monday = d - timedelta(days=d.weekday())
        slides = carousel.weekly(local, monday, alerts)
        stem, title = f"week_{monday.isoformat()}", f"Bergamo Nights · settimana {monday:%d/%m}"
    else:
        y, m = map(int, month.split("-"))
        slides = carousel.monthly(local, y, m, alerts)
        stem, title = f"month_{y}-{m:02d}", f"Bergamo Nights · {carousel.MESI[m]} {y}"
    paths = carousel.save(slides, outdir, stem)
    bundle = social.build_bundle(paths, [evs for _, evs in slides], title, outdir / f"{stem}_post.json")
    print(f"{len(paths)} slides -> {outdir}  (post bundle: {bundle.name})")


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
        print(f"  {len(html)} bytes, {html.count('application/ld+json')} JSON-LD blocks, "
              f"__NEXT_DATA__={'yes' if '__NEXT_DATA__' in html else 'no'}")
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
        make_carousel(cfg, store, a.week, a.month)


if __name__ == "__main__":
    main()
