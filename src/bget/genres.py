"""Give every night one main music genre.

Order of evidence: a hand-set genre for that night (config/genres.yaml) > genre words in the event itself (title, description, line-up genres) >
the promoter's usual genre (genres.yaml, then promoters.yaml) > the venue's usual genre (genres.yaml,
then venues.yaml) > "unspecified".
"""
from __future__ import annotations

import re

from .config import Config
from .models import Event

# label -> words that indicate it (checked in this order: specific before generic)
GENRES: list[tuple[str, str]] = [
    ("Hard techno", r"hard ?techno|hardtechno|industrial techno|schranz|tekno|free ?party|rave"),
    ("Melodic techno", r"melodic techno|melodic house|afterlife"),
    ("Techno", r"techno"),
    ("Tech house", r"tech ?-?house"),
    ("Afro house", r"afro ?house|afro|amapiano"),
    ("Deep house", r"deep ?house"),
    ("Minimal", r"minimal|microhouse|rominimal"),
    ("Drum & bass", r"drum ?(?:&|and|n) ?bass|dnb|jungle|bass music|dubstep"),
    ("Trance", r"trance|psytrance"),
    ("Disco / funk", r"disco|funk|nu ?disco|soul"),
    ("House", r"house"),
    ("Latin / reggaeton", r"reggaeton|latin[oa]?|salsa|bachata|perreo|dembow"),
    ("Hip hop / trap", r"hip ?hop|trap|rap|r&b|rnb|urban"),
    ("Revival", r"revival|anni ?(?:70|80|90)|70s|80s|90s|2000s|anni 2000"),
    ("Commercial", r"commerciale|commercial|hits|edm|top 40|mainstream"),
    ("Electronic", r"electronic|elettronica|club culture|clubbing"),
]
_RX = [(label, re.compile(rf"(?<![\w]){pat}(?![\w])", re.I)) for label, pat in GENRES]
UNSPECIFIED = "Unspecified"


def detect(text: str) -> str | None:
    for label, rx in _RX:
        if rx.search(text or ""):
            return label
    return None


def _from_list(genres: list[str] | None) -> str | None:
    for g in genres or []:
        if (label := detect(g)):
            return label
    return None


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def _pick(genres: list[str] | None) -> str | None:
    """Like _from_list, but a hand-set name (from genres.yaml, marked with a leading "=") is kept as written."""
    if genres and genres[0].startswith("="):
        return genres[0][1:]
    return _from_list(genres)


class GenreAssigner:
    def __init__(self, cfg: Config):
        ov = cfg.genre_overrides or {}
        venue_ov = {_norm(k): "=" + str(v) for k, v in (ov.get("venues") or {}).items() if v}
        promoter_ov = {_norm(k): "=" + str(v) for k, v in (ov.get("promoters") or {}).items() if v}
        self.nights = {}
        for n in ov.get("nights") or []:
            if n and n.get("venue") and n.get("date") and n.get("genre"):
                venue = cfg.match_venue(str(n["venue"]))
                self.nights[(_norm(venue.name if venue else str(n["venue"])), str(n["date"]))] = str(n["genre"])
        self.venue_genres = {}
        for v in cfg.venues:
            hand = next((venue_ov[_norm(a)] for a in [v["name"], *(v.get("aliases") or [])] if _norm(a) in venue_ov), None)
            self.venue_genres[_norm(v["name"])] = [hand] if hand else (v.get("genres") or [])
        for k, g in venue_ov.items():  # venues not in venues.yaml
            self.venue_genres.setdefault(k, [g])
        self.promoters = []
        for p in cfg.promoters:
            names = [p.get("name") or "", *(p.get("aliases") or [])]
            hand = next((promoter_ov[_norm(n)] for n in names if _norm(n) in promoter_ov), None)
            self.promoters.append(([n for n in names if len(n) > 3], p.get("instagram"),
                                   [hand] if hand else (p.get("genres") or [])))

    def assign(self, ev: Event) -> tuple[str, str]:
        """Returns (genre, where it came from)."""
        if (g := self.nights.get((_norm(ev.venue.name), ev.night.isoformat()))):
            return g, "manual"
        own = " ".join([ev.title, ev.description, " ".join(ev.genres), " ".join(g for a in ev.artists for g in a.genres)])
        if (g := detect(own)):
            return g, "event"
        handles = {h.lower() for h in ev.all_handles("instagram")}
        text = f"{ev.title} {ev.promoter_name or ''}".lower()
        for names, ig, genres in self.promoters:
            if (ig and ig.lower() in handles) or any(n.lower() in text for n in names):
                if (g := _pick(genres)):
                    return g, "promoter"
        if (g := _pick(self.venue_genres.get(_norm(ev.venue.name)))):
            return g, "venue"
        return UNSPECIFIED, "none"
