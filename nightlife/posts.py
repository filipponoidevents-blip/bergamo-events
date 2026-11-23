"""Le grafiche dei post (Instagram 4:5 e TikTok 9:16), con lo stesso disegno della versione 1.

    python -m nightlife posts --city bergamo                 # quelle in calendario oggi
    python -m nightlife posts --city bergamo --week 2026-11-23
    python -m nightlife posts --city bergamo --month 2026-12
    python -m nightlife posts --city bergamo --midweek 2026-11-25

Calendario (Filippo, 17/11/2026): settimanale ogni lunedì, "Prima della serata" ogni mercoledì (da giovedì a domenica),
mensile l'ultima domenica del mese (per il mese dopo). Nei post vanno solo le serate DJ/club; i locali con
in_posts: false (es. i grandi locali di Milano) restano solo nel calendario. Le immagini finiscono in out/post/.
"""
from __future__ import annotations

import calendar
import json
from datetime import date, timedelta

from . import carousel
from .city import City
from .posts_event import Event

DJ = ("dj set", "festival")
# tipi della nuova versione -> riquadri del post "Prima della serata"
MIDWEEK = {"aperitivo": "food_drink", "comedy": "comedy", "quiz": "quiz_games", "culture": "culture"}


def due(today: date) -> list[tuple[str, str]]:
    out = []
    if today.weekday() == 0:
        out.append(("week", today.isoformat()))
    if today.weekday() == 2:
        out.append(("midweek", today.isoformat()))
    last = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    if today.weekday() == 6 and today + timedelta(days=7) > last:
        nxt = last + timedelta(days=1)
        out.append(("month", f"{nxt.year}-{nxt.month:02d}"))
    return out


def make(city: City, kind: str, arg: str) -> list:
    path = city.out / f"{city.slug}_eventi.json"
    evs = [e for e in json.loads(path.read_text(encoding="utf-8")) if e.get("status") == "pubblicato" and e.get("in_posts", True)]
    page = city.c.get("page") or {}
    carousel.configure({"brand": page.get("brand"), "handle": page.get("handle"), "area": city.c.get("label")})
    local = [Event(e) for e in evs if e["scope"] == "zona" and e["type"] in DJ]
    alerts = [Event(e) for e in evs if e["scope"] != "zona" and e["type"] in DJ]
    pre = lambda e: e.raw["fascia"] == "preserata"  # noqa: E731
    groups = {k.upper(): v for k, v in (city.settings.get("genre_families") or {}).items()} or None
    if kind in ("week", "midweek"):
        d = date.fromisoformat(arg)
        monday = d - timedelta(days=d.weekday())
        if kind == "week":
            build = lambda fmt: carousel.weekly(local, monday, alerts, fmt, preserata=pre, genres=groups)  # noqa: E731
            stem = f"settimana_{monday.isoformat()}"
        else:
            others = [(MIDWEEK[e["type"]], Event(e)) for e in evs if e["scope"] == "zona" and e["type"] in MIDWEEK]
            build = lambda fmt: carousel.midweek(others, monday, fmt)  # noqa: E731
            stem = f"preserata_{monday.isoformat()}"
    else:
        y, m = (int(x) for x in arg.split("-"))
        build = lambda fmt: carousel.monthly(local, y, m, alerts, fmt, preserata=pre, genres=groups)  # noqa: E731
        stem = f"mese_{y}-{m:02d}"
    paths = []
    for fmt in ("ig", "tt"):
        paths += carousel.save(build(fmt), city.out / "post", f"{stem}_{fmt}")
    return paths
