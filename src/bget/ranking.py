"""Order of nights in the posts (Filippo, 2026-11-17): a score per night from five factors, each 0-10,
weighted in config/ranking.yaml:

  location   how important the venue is (venues: score)
  room       which room the night is in: main room 10, second rooms lower (venues: rooms)
  capacity   how many people the venue/room holds (venues: capacity, or the room's capacity)
  party      how relevant the party / promoter is (promoters: score)
  guest      special guests: a notable watchlist name 10, a "special guest" line 6, a line-up 3

rank = weighted average of the five, 0-10. A factor with no data counts 0, so filling in the file
moves a night up. Everything here is set by hand; nothing is guessed from followers or the like.
"""
from __future__ import annotations

import math
import re

import yaml

from .headliners import lineup_names
from .models import Event

GUEST_LINE = re.compile(r"special\s+guests?|guest\s*(?:dj|star)?\s*[:\-–—]|headliner|ospite\s+speciale|direttamente da", re.I)
DEFAULT_WEIGHTS = {"location": 1.0, "room": 0.5, "capacity": 1.0, "party": 1.5, "guest": 2.0}


def capacity_score(cap: float | None) -> float:
    """100 people -> 2, 400 -> 6, 1600+ -> 10."""
    if not cap:
        return 0.0
    return max(0.0, min(10.0, 2 * math.log2(cap / 50)))


class Ranker:
    def __init__(self, cfg):
        path = cfg.root / "config" / "ranking.yaml"
        data = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
        self.weights = {**DEFAULT_WEIGHTS, **{k: float(v) for k, v in (data.get("weights") or {}).items()}}
        self.default_room = {k.lower(): float(v) for k, v in (data.get("rooms") or {}).items() if v is not None}
        self.venues: dict[str, dict] = {}
        caps = {v["name"].lower(): v.get("capacity") for v in cfg.venues}
        for name, v in (data.get("venues") or {}).items():
            v = {"score": v} if not isinstance(v, dict) else dict(v)
            v.setdefault("capacity", caps.get(name.lower()))
            v["rooms"] = {k.lower(): (r if isinstance(r, dict) else {"score": r}) for k, r in (v.get("rooms") or {}).items()}
            self.venues[name.lower()] = v
        scores = {k.lower(): v for k, v in (data.get("promoters") or {}).items() if v is not None}
        self.promoters: list[tuple[list[str], str | None, float]] = []
        for p in cfg.promoters or []:
            if (s := scores.get(p["name"].lower())) is not None:
                names = [n for n in [p["name"], p.get("party_name"), *(p.get("aliases") or [])] if n and len(n) > 3]
                self.promoters.append((names, (p.get("instagram") or "").lower() or None, float(s)))
        wl = cfg.watchlist
        self.notable = [re.compile(rf"(?<!\w){re.escape(str(n))}(?!\w)", re.I)
                        for n in [*wl.get("dj_headliners", []), *wl.get("notable_artists", []),
                                  *wl.get("crossover_live", [])] if n]

    # ---- factors ------------------------------------------------------------------------------
    def _room(self, ev: Event, venue: dict) -> tuple[str | None, dict]:
        text = f"{ev.title}\n{ev.description[:400]}".lower()
        for name, r in venue.get("rooms", {}).items():
            if name in text:
                return name, r
        for name, s in self.default_room.items():
            if name in text:
                return name, {"score": s}
        return None, {}

    def party(self, ev: Event) -> float:
        handle = (ev.promoter.instagram or "").lower() if ev.promoter else ""
        text = f"{ev.title}\n{ev.promoter_name or ''}\n{ev.description[:400]}".lower()
        best = 0.0
        for names, ig, s in self.promoters:
            if (ig and (ig == handle or re.search(rf"(?<![\w.]){re.escape(ig)}(?!\w)", text))) or \
                    any(re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", text) for n in names):
                best = max(best, s)
        return best

    def guest(self, ev: Event) -> float:
        text = f"{ev.title}\n{ev.description}"
        if any(rx.search(text) for rx in self.notable):
            return 10.0
        if GUEST_LINE.search(text):
            return 6.0
        if lineup_names(ev.description) or ev.headliners:
            return 3.0
        return 0.0

    def factors(self, ev: Event) -> dict[str, float]:
        venue = self.venues.get(ev.venue.name.lower(), {})
        room_name, room = self._room(ev, venue)
        cap = room.get("capacity") or (venue.get("capacity") if not room_name or room.get("score", 10) >= 10 else None)
        return {
            "location": float(venue.get("score") or 0),
            "room": float(room.get("score", 10)),   # no room named: the main room
            "capacity": capacity_score(cap),
            "party": self.party(ev),
            "guest": self.guest(ev),
        }

    def rank(self, ev: Event) -> float:
        f = self.factors(ev)
        total = sum(self.weights.values()) or 1
        return round(sum(self.weights[k] * f[k] for k in f) / total, 2)
