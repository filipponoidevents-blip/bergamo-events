"""ICS feed writer (RFC 5545). Subscribe to the output URL from Google/Apple Calendar."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from .models import Event


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    b = line.encode()
    if len(b) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        c = ch.encode()
        if len(cur) + len(c) > (75 if not parts else 74):
            parts.append(cur.decode())
            cur = b""
        cur += c
    parts.append(cur.decode())
    return "\r\n ".join(parts)


def _utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def describe(ev: Event) -> str:
    lines = []
    if ev.artists:
        lines.append("Line-up: " + ", ".join(a.name for a in ev.artists))
    if ev.genres:
        lines.append("Genres: " + ", ".join(ev.genres))
    if ev.price:
        lines.append("Price: " + ev.price)
    if ev.alert_level:
        dist = f" ({ev.distance_km} km from Bergamo)" if ev.distance_km is not None else ""
        lines.append(f"ALERT: {ev.alert_level}{dist}")
    if ev.ticket_url:
        lines.append("Tickets: " + ev.ticket_url)
    handles = ev.all_handles("instagram")
    if handles:
        lines.append("IG: " + " ".join("@" + h for h in handles))
    if ev.description:
        lines += ["", ev.description[:800]]
    return "\n".join(lines)


def build_ics(events: Iterable[Event], name: str, prefix: str = "",
              reminders: Iterable[tuple] = ()) -> str:
    """reminders: (uid, date, summary, description) all-day notes, e.g. token renewal."""
    now = _utc(datetime.now(timezone.utc))
    out = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//bergamo-events-tracker//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(name)}", "X-WR-TIMEZONE:Europe/Rome",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
    ]
    for ev in events:
        end = ev.end or ev.start + timedelta(hours=5)
        loc = ", ".join(x for x in [ev.venue.name, ev.venue.address, ev.venue.city] if x)
        out += [
            "BEGIN:VEVENT",
            f"UID:{ev.uid}@bergamo-events",
            f"DTSTAMP:{now}",
            f"DTSTART:{_utc(ev.start)}",
            f"DTEND:{_utc(end)}",
            f"SUMMARY:{_esc(prefix + ev.title)}",
            f"LOCATION:{_esc(loc)}",
            f"DESCRIPTION:{_esc(describe(ev))}",
            f"CATEGORIES:{_esc(ev.category.value)}",
        ]
        if ev.url:
            out.append(f"URL:{ev.url}")
        if ev.venue.lat is not None:
            out.append(f"GEO:{ev.venue.lat};{ev.venue.lon}")
        out.append("END:VEVENT")
    for uid, day, summary, desc in reminders:
        out += [
            "BEGIN:VEVENT", f"UID:{uid}@bergamo-events", f"DTSTAMP:{now}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_esc(summary)}", f"DESCRIPTION:{_esc(desc)}", "END:VEVENT",
        ]
    out.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in out) + "\r\n"
