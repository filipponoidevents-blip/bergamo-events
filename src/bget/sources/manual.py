"""Hand-entered events (YAML). The fallback for anything announced only on Instagram/Facebook."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import yaml

from ..models import Artist, Category, Event, Socials, Venue
from .base import Source


class ManualSource(Source):
    type = "manual"

    def collect(self) -> Iterable[Event]:
        tz = ZoneInfo(self.cfg.settings.get("timezone", "Europe/Rome"))
        path = self.cfg.root / self.spec["path"]
        if not path.exists():
            return
        for e in (yaml.safe_load(path.read_text()) or {}).get("events", []):
            v = e["venue"]
            venue = (self.cfg.match_venue(v) if isinstance(v, str) else None) or (
                Venue(name=v, city="") if isinstance(v, str)
                else Venue(**{**v, "socials": Socials(**(v.get("socials") or {}))})
            )
            start = datetime.fromisoformat(str(e["start"]))
            end = datetime.fromisoformat(str(e["end"])) if e.get("end") else None
            yield Event(
                title=e["title"],
                start=start if start.tzinfo else start.replace(tzinfo=tz),
                end=(end if end is None or end.tzinfo else end.replace(tzinfo=tz)),
                venue=venue,
                artists=[
                    Artist(name=a, socials=Socials()) if isinstance(a, str)
                    else Artist(**{**a, "socials": Socials(**(a.get("socials") or {}))})
                    for a in e.get("artists", [])
                ],
                genres=e.get("genres", []),
                category=Category(e.get("category", "club")),
                description=e.get("description", ""),
                url=e.get("url"),
                ticket_url=e.get("ticket_url"),
                image_url=e.get("image_url"),
                price=e.get("price"),
                promoter_name=e.get("promoter"),
                promoter=Socials(**e["promoter_socials"]) if e.get("promoter_socials") else None,
                sources=[self.id],
                fascia=e.get("fascia"),  # serata | preserata, optional
            )
