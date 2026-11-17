"""Order of nights in the posts (Filippo, 2026-11-17): a score for venues, rooms and promoters,
set by hand in config/ranking.yaml.

rank = max(venue score, promoter score) + room adjustment + 1 per notable watchlist artist (max +2).
"""
from __future__ import annotations

import re

import yaml

from .models import Event


class Ranker:
    def __init__(self, cfg):
        path = cfg.root / "config" / "ranking.yaml"
        data = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
        self.venues = {k.lower(): float(v) for k, v in (data.get("venues") or {}).items() if v is not None}
        self.rooms = [(k.lower(), float(v)) for k, v in (data.get("rooms") or {}).items() if v is not None]
        self.default = float(data.get("default", 4))
        # promoter score by name, alias and handle
        scores = {k.lower(): float(v) for k, v in (data.get("promoters") or {}).items() if v is not None}
        self.promoters: list[tuple[list[str], str | None, float]] = []
        for p in cfg.promoters or []:
            if (s := scores.get(p["name"].lower())) is not None:
                names = [n for n in [p["name"], p.get("party_name"), *(p.get("aliases") or [])] if n and len(n) > 3]
                self.promoters.append((names, (p.get("instagram") or "").lower() or None, s))
        wl = cfg.watchlist
        self.notable = [re.compile(rf"(?<!\w){re.escape(str(n))}(?!\w)", re.I)
                        for n in [*wl.get("dj_headliners", []), *wl.get("notable_artists", []),
                                  *wl.get("crossover_live", [])] if n]

    def venue_score(self, ev: Event) -> float:
        return self.venues.get(ev.venue.name.lower(), self.default)

    def promoter_score(self, ev: Event) -> float | None:
        handle = (ev.promoter.instagram or "").lower() if ev.promoter else ""
        text = f"{ev.title}\n{ev.promoter_name or ''}".lower()
        best = None
        for names, ig, s in self.promoters:
            if (ig and ig == handle) or any(re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", text) for n in names):
                best = s if best is None else max(best, s)
        return best

    def rank(self, ev: Event) -> float:
        score = max(self.venue_score(ev), self.promoter_score(ev) or 0)
        text = f"{ev.title}\n{ev.description[:300]}".lower()
        room = next((adj for name, adj in self.rooms if name in text), 0.0)
        heads = sum(1 for rx in self.notable if rx.search(f"{ev.title}\n{ev.description}"))
        return round(score + room + min(heads, 2), 1)
