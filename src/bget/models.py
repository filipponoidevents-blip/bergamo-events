"""Core data model.

Everything that later phases need (calendar, carousels, auto-posting with tags)
lives here, so each collector only has to fill these objects in.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Category(str, Enum):
    CLUB = "club"            # club night / DJ set
    FESTIVAL = "festival"    # multi-act electronic or music festival
    CONCERT = "concert"      # live show (only kept if crossover headliner)
    PARTY = "party"          # open air, day party, warehouse, pop-up
    OTHER = "other"


class Scope(str, Enum):
    LOCAL = "local"          # inside Bergamo +20 km -> main calendar
    ALERT = "alert"          # outside the radius but big enough to flag
    IGNORE = "ignore"        # filtered out


@dataclass
class Socials:
    """Handles without the @. These become the tags in Phase 3 posts."""
    instagram: str | None = None
    tiktok: str | None = None
    facebook: str | None = None
    website: str | None = None
    resident_advisor: str | None = None

    def merge(self, other: "Socials") -> "Socials":
        return Socials(**{k: getattr(self, k) or getattr(other, k) for k in asdict(self)})


@dataclass
class Venue:
    name: str
    city: str
    province: str | None = None          # Italian sigla: BG, MI, BS, LC, CO, CR, MB...
    lat: float | None = None
    lon: float | None = None
    address: str | None = None
    kind: str = "club"                   # club | live_club | open_air | arena | bar | other
    capacity: int | None = None
    socials: Socials = field(default_factory=Socials)
    verified: bool = False               # set True once coordinates/handles are checked by hand

    @property
    def key(self) -> str:
        return slug(f"{self.name}-{self.city}")


@dataclass
class Artist:
    name: str
    role: str = "dj"                     # dj | live | headliner | support
    genres: list[str] = field(default_factory=list)
    socials: Socials = field(default_factory=Socials)

    @property
    def key(self) -> str:
        return slug(self.name)


@dataclass
class Event:
    title: str
    start: datetime                      # timezone-aware, Europe/Rome
    venue: Venue
    end: datetime | None = None
    artists: list[Artist] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    genre: str | None = None             # one main genre for the night (genres.py)
    genre_source: str | None = None      # event | promoter | venue | none
    headliners: list[str] = field(default_factory=list)  # notable names found in the post (headliners.py)
    # serata / preserata clues (fascia.py decides)
    fascia: str | None = None            # set by hand: "serata" | "preserata" (wins over everything)
    fascia_hint: str | None = None       # what the post says: "aperitivo" -> preserata, "after hour" -> serata
    start_known: bool = True             # False when no start time was written (start is a 23:00 placeholder)
    late_end: bool = False               # "till late", "fino all'alba": ends late, no time given
    entry: str | None = None             # "paid" (list, price, tickets) | "free" (ingresso libero) | None
    fascia_auto: str | None = None       # result of fascia.classify, refreshed every run
    fascia_why: str | None = None        # its reason, in Italian
    in_posts: bool = True               # False: calendar only, never in the graphics (venues.yaml in_posts)
    rank: float = 0.0                    # order in the posts, higher first (ranking.py, config/ranking.yaml)
    category: Category = Category.OTHER
    description: str = ""
    url: str | None = None               # canonical event page
    ticket_url: str | None = None
    image_url: str | None = None
    price: str | None = None
    promoter: Socials | None = None      # organiser / collective handles
    promoter_name: str | None = None
    room: str | None = None              # a second room ("club room"): its own night, apart from the main room
    sources: list[str] = field(default_factory=list)   # source ids that reported it
    source_urls: list[str] = field(default_factory=list)
    # filled by relevance.py
    score: float = 0.0
    scope: Scope = Scope.IGNORE
    distance_km: float | None = None
    reasons: list[str] = field(default_factory=list)
    alert_level: str | None = None       # regional | national
    first_seen: datetime | None = None
    updated: datetime | None = None

    @property
    def uid(self) -> str:
        """Stable id: same venue + same day + similar title => same event, across sources."""
        base = f"{self.venue.key}|{self.night.isoformat()}|{slug(self.title)[:40]}"
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    @property
    def dedupe_key(self) -> str:
        """Looser key used to merge the same night reported by RA, Dice and the venue."""
        base = f"{self.venue.key}|{self.night.isoformat()}"
        return f"{base}|{slug(self.room)}" if self.room else base

    @property
    def night(self):
        """The 'party night' date: a 01:00 start belongs to the previous evening."""
        from datetime import timedelta
        return (self.start - timedelta(hours=6)).date()

    def all_handles(self, network: str = "instagram") -> list[str]:
        """Venue, promoter and artist handles for tagging, de-duplicated, in that order."""
        out: list[str] = []
        for s in [self.venue.socials, self.promoter or Socials(), *[a.socials for a in self.artists]]:
            h = getattr(s, network)
            if h and h not in out:
                out.append(h)
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat() if self.end else None
        d["first_seen"] = self.first_seen.isoformat() if self.first_seen else None
        d["updated"] = self.updated.isoformat() if self.updated else None
        d["category"] = self.category.value
        d["scope"] = self.scope.value
        d["uid"] = self.uid
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        d = dict(d)
        d.pop("uid", None)
        v = d.pop("venue")
        venue = Venue(**{**v, "socials": Socials(**(v.get("socials") or {}))})
        artists = [Artist(**{**a, "socials": Socials(**(a.get("socials") or {}))}) for a in d.pop("artists", [])]
        promoter = Socials(**d.pop("promoter")) if d.get("promoter") else None
        d.pop("promoter", None)
        for k in ("start", "end", "first_seen", "updated"):
            if d.get(k):
                d[k] = datetime.fromisoformat(d[k])
        return cls(
            venue=venue, artists=artists, promoter=promoter,
            category=Category(d.pop("category", "other")), scope=Scope(d.pop("scope", "ignore")),
            **d,
        )


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
