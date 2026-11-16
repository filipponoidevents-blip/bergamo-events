"""ICS feed writer (RFC 5545). Subscribe to the output URL from Google/Apple Calendar."""
from __future__ import annotations

import re

from zoneinfo import ZoneInfo
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


ROME = ZoneInfo("Europe/Rome")


def calendar_start(ev: Event) -> datetime:
    """Only the start goes on the calendar. A night that starts after midnight (00:00-05:59) stays on
    the evening it belongs to, at 23:59: a Saturday 00:30 start shows on Saturday, not Sunday."""
    local = ev.start.astimezone(ROME)
    if local.hour < 6:
        n = ev.night
        return datetime(n.year, n.month, n.day, 23, 59, tzinfo=ROME)
    return local


def party_name(ev: Event) -> str:
    """The night's own name, or "" when the title is only a date or the venue's name."""
    from .sources.instagram_rules import _strip_date_words
    t = (ev.title or "").strip()
    rest = _strip_date_words(t).lower()
    for w in re.findall(r"\w+", ev.venue.name.lower()):
        rest = rest.replace(w, "")
    if len(rest) < 4:
        return ""
    return t if len(t) <= 45 else t[:44].rsplit(" ", 1)[0] + "…"


def night_summary(ev: Event, prefix: str = "") -> str:
    """Naming convention (Filippo, 2026-11-16): "[Genre] Venue · Party name w/ Headliners", leaving out
    the parts that are missing (Bolgia rarely has a party name, but has a headliner)."""
    genre = f"[{ev.genre}] " if ev.genre and ev.genre != "Unspecified" else ""
    name = " · ".join(p for p in [ev.venue.name, party_name(ev)] if p)
    head = headliners(ev)
    return prefix + genre + name + (f" w/ {head}" if head else "")


def headliners(ev: Event, n: int = 2) -> str:
    names = ev.headliners[:n]
    return ", ".join(names) + (" + altri" if len(ev.headliners) > n else "")


def describe(ev: Event) -> str:
    """Details, always in this order, Italian labels; lines with nothing to say are left out."""
    local = ev.start.astimezone(ROME)
    lines = [f"Inizio: {local:%H:%M}" + (" (dopo mezzanotte)" if local.hour < 6 else "")]
    lines.append("Locale: " + ", ".join(x for x in [ev.venue.name, ev.venue.city] if x))
    if ev.promoter_name:
        lines.append("Promoter: " + ev.promoter_name)
    if ev.headliners:
        lines.append("Headliner: " + ", ".join(ev.headliners))
    if ev.artists:
        lines.append("Line-up: " + ", ".join(a.name for a in ev.artists))
    if ev.genre and ev.genre != "Unspecified":
        lines.append(f"Genere: {ev.genre}" + {"venue": " (genere abituale del locale)",
                                             "promoter": " (genere abituale del promoter)"}.get(ev.genre_source or "", ""))
    if ev.price:
        lines.append("Prezzo: " + ev.price)
    if ev.ticket_url:
        lines.append("Biglietti: " + ev.ticket_url)
    if ev.url:
        lines.append("Post: " + ev.url)
    if ev.alert_level:
        dist = f" ({ev.distance_km} km da Bergamo)" if ev.distance_km is not None else ""
        lines.append(f"ALERT: {ev.alert_level}{dist}")
    handles = ev.all_handles("instagram")
    if handles:
        lines.append("Tag: " + " ".join("@" + h for h in handles))
    if ev.description:
        lines += ["", "—", ev.description[:1200]]
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
        loc = ", ".join(x for x in [ev.venue.name, ev.venue.address, ev.venue.city] if x)
        out += [
            "BEGIN:VEVENT",
            f"UID:{ev.uid}@bergamo-events",
            f"DTSTAMP:{now}",
            f"DTSTART:{_utc(calendar_start(ev))}",  # no DTEND: start time only (Filippo)
            f"SUMMARY:{_esc(night_summary(ev, prefix))}",
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
