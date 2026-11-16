"""Give every night one main music genre.

Order of evidence: genre words in the event itself (title, description, line-up genres) >
the promoter's usual genre (promoters.yaml) > the venue's usual genre (venues.yaml) > "unspecified".
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


class GenreAssigner:
    def __init__(self, cfg: Config):
        self.venue_genres = {v["name"]: v.get("genres") or [] for v in cfg.venues}
        self.promoters = []
        for p in cfg.promoters:
            names = [p.get("name") or "", *(p.get("aliases") or [])]
            self.promoters.append(([n for n in names if len(n) > 3], p.get("instagram"), p.get("genres") or []))

    def assign(self, ev: Event) -> tuple[str, str]:
        """Returns (genre, where it came from)."""
        own = " ".join([ev.title, ev.description, " ".join(ev.genres), " ".join(g for a in ev.artists for g in a.genres)])
        if (g := detect(own)):
            return g, "event"
        handles = {h.lower() for h in ev.all_handles("instagram")}
        text = f"{ev.title} {ev.promoter_name or ''}".lower()
        for names, ig, genres in self.promoters:
            if (ig and ig.lower() in handles) or any(n.lower() in text for n in names):
                if (g := _from_list(genres)):
                    return g, "promoter"
        if (g := _from_list(self.venue_genres.get(ev.venue.name))):
            return g, "venue"
        return UNSPECIFIED, "none"
