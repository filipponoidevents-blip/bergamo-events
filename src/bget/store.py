"""JSON event store with cross-source merging.

A JSON file in git is enough at this scale (hundreds of events/month) and gives a free
change history. Swap for SQLite if it grows.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import Event

# details a source re-reads on every run; a newer reading replaces what is stored
REREAD_FIELDS = ("title", "start", "end", "fascia", "fascia_hint", "start_known", "late_end", "entry")


def merge(a: Event, b: Event) -> Event:
    """Combine two reports of the same night. `a` wins on conflicts, `b` fills gaps."""
    known = {x.key: x for x in a.artists}
    for art in b.artists:
        if art.key in known:
            known[art.key].socials = known[art.key].socials.merge(art.socials)
            known[art.key].genres = sorted(set(known[art.key].genres) | set(art.genres))
        else:
            a.artists.append(art)
            known[art.key] = art
    a.venue.socials = a.venue.socials.merge(b.venue.socials)
    for f in ("end", "url", "ticket_url", "image_url", "price", "promoter_name", "promoter",
              "fascia", "fascia_hint", "entry"):
        if not getattr(a, f) and getattr(b, f):
            setattr(a, f, getattr(b, f))
    if len(b.description) > len(a.description):
        a.description = b.description
    a.late_end = a.late_end or b.late_end
    if not a.start_known and b.start_known:
        a.start, a.start_known = b.start, True
    a.genres = sorted(set(a.genres) | set(b.genres))
    a.sources = sorted(set(a.sources) | set(b.sources))
    a.source_urls = sorted(set(a.source_urls) | set(b.source_urls))
    if a.category.value == "other":
        a.category = b.category
    return a


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.events: dict[str, Event] = {}
        if path.exists():
            for d in json.loads(path.read_text()):
                ev = Event.from_dict(d)
                self.events[ev.dedupe_key] = ev

    def upsert(self, ev: Event) -> str:
        now = datetime.now(timezone.utc)
        k = ev.dedupe_key
        # the same report filed under another key before (e.g. before its room was known): take it out there
        # (only per-post ids like "ig:123-0"; "eco_di_bergamo" names a whole site, shared by many events)
        own = {s for s in ev.sources if s.startswith("ig:")}
        for k2, other in list(self.events.items()) if own else []:
            if k2 != k and set(other.sources) & own:
                other.sources = [s for s in other.sources if s not in own]
                if not other.sources:
                    del self.events[k2]
        if k in self.events:
            old = self.events[k]
            if set(ev.sources) & set(old.sources):
                # the same source reporting again (e.g. a caption re-read by a better reader):
                # its newer reading of the details replaces the old one
                for f in REREAD_FIELDS:
                    setattr(old, f, getattr(ev, f))
            merge(old, ev)
            old.updated = now
            return "updated"
        ev.first_seen = ev.updated = now
        self.events[k] = ev
        return "new"

    def drop_sources(self, source_ids: set[str]) -> int:
        """Remove a source's reports (e.g. rejected Instagram posts); drop events left with no source."""
        if not source_ids:
            return 0
        removed = 0
        for k, ev in list(self.events.items()):
            if set(ev.sources) & source_ids:
                ev.sources = [s for s in ev.sources if s not in source_ids]
                if not ev.sources:
                    del self.events[k]
                    removed += 1
        return removed

    def hide(self, rules: list[dict]) -> int:
        """Remove events matching config/hidden.yaml (Filippo's "togli")."""
        removed = 0
        for k, ev in list(self.events.items()):
            text = f"{ev.title}\n{ev.description}".lower()
            for r in rules:
                if (not r.get("text") or str(r["text"]).lower() in text) and \
                        (not r.get("venue") or str(r["venue"]).lower() == ev.venue.name.lower()) and \
                        (not r.get("date") or str(r["date"]) == ev.start.date().isoformat()) and \
                        any(r.get(f) for f in ("text", "venue", "date")):
                    del self.events[k]
                    removed += 1
                    break
        return removed

    def all(self) -> list[Event]:
        return sorted(self.events.values(), key=lambda e: e.start)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([e.to_dict() for e in self.all()], ensure_ascii=False, indent=2))
