"""Minimal .ics feed reader (VEVENT with SUMMARY/DTSTART/DTEND/LOCATION/URL/DESCRIPTION)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from ..models import Category, Event, Venue
from .base import Source


def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _dt(name: str, value: str, tz: ZoneInfo) -> datetime:
    params = dict(p.split("=", 1) for p in name.split(";")[1:] if "=" in p)
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    fmt = "%Y%m%dT%H%M%S" if "T" in value else "%Y%m%d"
    return datetime.strptime(value, fmt).replace(tzinfo=ZoneInfo(params["TZID"]) if "TZID" in params else tz)


class ICalSource(Source):
    type = "ical"

    def collect(self) -> Iterable[Event]:
        tz = ZoneInfo(self.cfg.settings.get("timezone", "Europe/Rome"))
        for url in self.spec.get("urls", []):
            cur: dict[str, tuple[str, str]] | None = None
            for line in _unfold(self.fetch(url)):
                if line == "BEGIN:VEVENT":
                    cur = {}
                elif line == "END:VEVENT" and cur is not None:
                    if "SUMMARY" in cur and "DTSTART" in cur:
                        loc = _unescape(cur.get("LOCATION", ("", ""))[1])
                        name, _, city = loc.partition(",")
                        yield Event(
                            title=_unescape(cur["SUMMARY"][1]),
                            start=_dt(*cur["DTSTART"], tz),
                            end=_dt(*cur["DTEND"], tz) if "DTEND" in cur else None,
                            venue=Venue(name=name.strip() or "Unknown venue", city=city.strip().split(",")[0]),
                            description=_unescape(cur.get("DESCRIPTION", ("", ""))[1]),
                            url=cur.get("URL", ("", None))[1],
                            category=Category.OTHER,
                            sources=[self.id],
                            source_urls=[url],
                        )
                    cur = None
                elif cur is not None and ":" in line:
                    name, value = line.split(":", 1)
                    cur[name.split(";")[0]] = (name, value)
