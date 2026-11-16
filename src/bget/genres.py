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
    ("Hardstyle / hardcore", r"hard ?style|hardcore|gabber|frenchcore|uptempo|hard ?dance"),
    ("Hard techno", r"hard ?techno|hardtechno|industrial techno|industrial|schranz|tekno|free ?party|rave|"
                    r"hard ?groove|acid techno"),
    ("Melodic techno", r"melodic ?techno|melodic ?house|melodic|afterlife|progressive house|progressive|"
                       r"organic house|indie dance"),
    ("Techno", r"techno(?!log)|technoitalia"),
    ("Tech house", r"tech ?-?house|techhouse"),
    ("Afro house", r"afro ?house|afro ?tech|afro|afrobeats?|amapiano"),
    ("Deep house", r"deep ?house|deephouse|deep"),
    ("Minimal", r"minimal|microhouse|rominimal"),
    ("Drum & bass", r"drum ?(?:&|and|n|'n'|’n’) ?bass|dnb|d&b|jungle|bass music|dubstep|neurofunk|liquid|"
                    r"breakbeat|breaks|uk ?garage|ukg"),
    ("Trance", r"trance|psytrance|psy|goa"),
    ("Disco / funk", r"disco(?!tec)|discomusic|italo ?disco|nu ?disco|funk|funky|soul|boogie|cosmic"),
    ("House", r"house|housemusic|house music|jackin|soulful|french touch|piano house"),
    ("Latin / reggaeton", r"reggaeton|regueton|latin[oa]?|latin music|salsa|bachata|kizomba|perreo|dembow|"
                          r"cubana|dancehall|moombahton|baile"),
    ("Hip hop / trap", r"hip ?-?hop|trap|rap(?!ture|id)|r&b|rnb|r'n'b|drill|urban"),
    ("Revival", r"revival|remember|anni ?'?(?:60|70|80|90)|'?(?:70|80|90)s|'?(?:70|80|90)'s|2000s|anni 2000|"
                r"old ?school|eurodance|italo ?dance"),
    ("Commerciale", r"commerciale|commercial|hits|top hits|edm|top 40|mainstream|pop(?!-?up)|reggaeton e commerciale|"
                   r"dance commerciale"),
    ("Electronic", r"electronic|elettronica|club culture|clubbing|underground|electronic music|musica elettronica"),
]
# whole words in text; in hashtags, also as a prefix (#technobergamo, #housemusiclovers)
_RX = [(label, re.compile(rf"(?<![\w]){pat}(?![\w])", re.I)) for label, pat in GENRES]
_RX_TAG = [(label, re.compile(rf"#(?:{pat})", re.I)) for label, pat in GENRES]
UNSPECIFIED = "Unspecified"


def detect(text: str) -> str | None:
    found = detect_all(text)
    return found[0] if found else None


def detect_all(text: str) -> list[str]:
    """Every genre the text mentions, most specific first."""
    text = text or ""
    return [label for (label, rx), (_, tag) in zip(_RX, _RX_TAG) if rx.search(text) or tag.search(text)]


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


# Promoter names that are also everyday words: matched only by Instagram handle, never by text,
# so "dress code" or "spicy margarita" don't pick a promoter's genre.
AMBIGUOUS_NAMES = {"code", "merci", "savage", "spicy", "honey", "pitch", "offline", "alive", "lova", "icon",
                   "talk", "please", "step up", "the circle", "side a", "lipstick", "choose me", "naif",
                   "the box bergamo", "upside down", "club culture", "open bar night", "enter the void"}


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
            self.promoters.append(([n for n in names if len(n) > 3 and n.lower() not in AMBIGUOUS_NAMES], p.get("instagram"),
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
            if (ig and ig.lower() in handles) or any(re.search(rf"(?<![\w]){re.escape(n.lower())}(?![\w])", text) for n in names):
                if (g := _pick(genres)):
                    return g, "promoter"
        if (g := _pick(self.venue_genres.get(_norm(ev.venue.name)))):
            return g, "venue"
        return UNSPECIFIED, "none"
