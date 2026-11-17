"""Serata or preserata (Filippo, 2026-11-17).

A serata starts at 23:30 or later and ends at 03:00 or later; anything earlier that ends by 03:00
is a preserata. Most posts give no end time, so when the times can't decide, other clues are used,
in this order:

1. set by hand: `fascia:` in config/ig_decisions.yaml (by Instagram id) or in manual_events.yaml
   big music events (festivals, crossover concerts, regional/national alerts) -> always serata
2. the end time, when written: from 03:00 on -> serata, earlier -> preserata ("till late" counts as late)
3. the start time: from 23:30 (or after midnight) -> serata; before 22:00 -> preserata
4. what the post says: "aperitivo", "preserata" -> preserata; "after hour", "fino all'alba" -> serata
5. the price: a list, a price or tickets -> serata; free entry for everyone -> preserata
6. the venue: `fascia:` in config/venues.yaml (serata | preserata; "orari" = mixed venue, times only),
   else clubs -> serata and bars/lounges -> preserata
7. nothing to go on: serata, marked "da verificare" in out/serata_preserata.md

Thresholds are in settings.yaml under `serata:` (start_from, end_from, early_before).
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

SERATA, PRESERATA = "serata", "preserata"
KIND_DEFAULT = {"club": SERATA, "open_air": SERATA, "bar": PRESERATA}


def _minutes(limit: str) -> int:
    h, m = map(int, str(limit).split(":"))
    return (h + 24 if h < 7 else h) * 60 + m


def _night(t, tz) -> int:
    t = t.astimezone(tz)
    return (t.hour + 24 if t.hour < 7 else t.hour) * 60 + t.minute


def venue_fascia(cfg, venue) -> str | None:
    """`fascia:` written on the venue in venues.yaml, else a default from its kind."""
    names = {venue.name.lower()}
    for v in cfg.venues:
        if {v["name"].lower(), *[a.lower() for a in v.get("aliases") or []]} & names:
            if v.get("fascia") in (SERATA, PRESERATA):
                return v["fascia"]
            if v.get("fascia") == "orari":
                return None  # mixed venue (live clubs, arenas, open air): only the times decide
            return KIND_DEFAULT.get(v.get("kind"))
    return None  # not in the registry


def classify(ev, cfg) -> tuple[str, str]:
    """("serata" | "preserata", short reason in Italian)."""
    rule = cfg.settings.get("serata") or {}
    tz = ZoneInfo(cfg.settings.get("timezone", "Europe/Rome"))
    start_from = _minutes(rule.get("start_from", "23:30"))
    end_from = _minutes(rule.get("end_from", "03:00"))
    early = _minutes(rule.get("early_before", "22:00"))

    if ev.fascia in (SERATA, PRESERATA):
        return ev.fascia, "deciso a mano"
    if getattr(ev.category, "value", ev.category) in ("festival", "concert") or ev.alert_level:
        return SERATA, "grande evento musicale"  # Filippo: big music events are a serata a priori
    if ev.end:
        e = _night(ev.end, tz)
        return (SERATA, f"finisce alle {ev.end.astimezone(tz):%H:%M}") if e >= end_from else \
               (PRESERATA, f"finisce alle {ev.end.astimezone(tz):%H:%M}")
    if ev.late_end:
        return SERATA, "finisce tardi (till late / fino all'alba)"
    if ev.start_known:
        s = _night(ev.start, tz)
        if s >= start_from:
            return SERATA, f"inizia alle {ev.start.astimezone(tz):%H:%M}"
        if s < early:
            return PRESERATA, f"inizia alle {ev.start.astimezone(tz):%H:%M}"
    if ev.fascia_hint in (SERATA, PRESERATA):
        return ev.fascia_hint, "lo dice il post"
    if ev.entry == "paid":
        return SERATA, "lista / prezzo / biglietti"
    if ev.entry == "free":
        return PRESERATA, "ingresso libero"
    if (v := venue_fascia(cfg, ev.venue)):
        return v, "tipo di locale"
    return SERATA, "nessun indizio, da verificare"


def is_preserata(ev, cfg) -> bool:
    return classify(ev, cfg)[0] == PRESERATA


def write_report(root, events) -> str:
    """out/serata_preserata.md: every upcoming local night with its times and how it was classified,
    so a wrong one can be fixed with `fascia:` in config/ig_decisions.yaml."""
    from .calendar_ics import ROME, night_summary

    evs = sorted(events, key=lambda e: e.start)
    n = len(evs) or 1
    with_start = sum(e.start_known for e in evs)
    with_end = sum(bool(e.end) or e.late_end for e in evs)
    lines = ["# Serata / preserata", "",
             f"{len(evs)} serate in calendario. Orario di inizio scritto: {with_start} ({with_start * 100 // n}%). "
             f"Orario di fine (o \"till late\"): {with_end} ({with_end * 100 // n}%).", "",
             "Per correggere una serata: in `config/ig_decisions.yaml`, sotto `fascia:`, scrivi `\"<id>\": preserata` "
             "(o `serata`). Per un locale: `fascia: serata` o `fascia: preserata` in `config/venues.yaml`.", ""]
    for label in (SERATA, PRESERATA):
        sel = [e for e in evs if e.fascia_auto == label]
        lines += [f"## {label.capitalize()} ({len(sel)})", ""]
        for e in sel:
            s = e.start.astimezone(ROME)
            when = f"{s:%a %d/%m} " + (f"{s:%H:%M}" if e.start_known else "--:--")
            when += f"–{e.end.astimezone(ROME):%H:%M}" if e.end else ("–tardi" if e.late_end else "")
            ids = ", ".join(f"`{x[3:]}`" for x in e.sources if x.startswith("ig:")) or e.sources[0]
            lines.append(f"- {when} **{night_summary(e)}** · {e.fascia_why} · {ids}")
        lines.append("")
    text = "\n".join(lines)
    (root / "out").mkdir(exist_ok=True)
    (root / "out" / "serata_preserata.md").write_text(text)
    return text
