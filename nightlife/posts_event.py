"""Un evento della nuova versione visto come lo vuole il disegnatore dei post (carousel.py, preso dalla versione 1)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")


@dataclass
class Venue:
    name: str
    city: str | None = None


class Event:
    def __init__(self, e: dict):
        self.raw = e
        self.night: date = date.fromisoformat(e["date"])
        self.start_known = bool(e.get("start"))
        hh, mm = (int(x) for x in (e.get("start") or "00:00").split(":")[:2])
        day = self.night + timedelta(days=1) if (hh < 6 and self.start_known) or not self.start_known else self.night
        self.start = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ROME)
        self.venue = Venue(e["venue"], e.get("city"))
        self.genre = e.get("genre")
        self.title = e.get("party") or e.get("headliner") or e["venue"]
        self.rank = e.get("score") or 0
        self.room = e.get("room") if e.get("room") and e.get("room") != "main room" else None


def headliners(e: Event) -> str:
    return e.raw.get("headliner") or ""


def party_name(e: Event) -> str:
    return e.raw.get("party") or ""
