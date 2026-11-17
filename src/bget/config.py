from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import Socials, Venue, slug

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    settings: dict[str, Any]
    sources: list[dict[str, Any]]
    watchlist: dict[str, list[str]]
    venues: list[dict[str, Any]]
    towns: dict[str, dict[str, Any]]
    promoters: list[dict[str, Any]] = None  # type: ignore[assignment]
    genre_overrides: dict[str, Any] = None  # type: ignore[assignment]
    root: Path = ROOT

    @classmethod
    def load(cls, root: Path = ROOT) -> "Config":
        def y(name: str) -> Any:
            return yaml.safe_load((root / "config" / name).read_text()) or {}
        v = y("venues.yaml")
        return cls(
            settings=y("settings.yaml"),
            sources=y("sources.yaml").get("sources", []),
            watchlist={k: list(vs or []) for k, vs in y("watchlist.yaml").items()},
            venues=v.get("venues", []),
            towns=v.get("towns", {}),
            promoters=(y("promoters.yaml").get("promoters", []) if (root / "config" / "promoters.yaml").exists() else []),
            genre_overrides=(y("genres.yaml") if (root / "config" / "genres.yaml").exists() else {}),
            root=root,
        )

    # ---- venue registry -------------------------------------------------
    def match_venue(self, name: str, city: str | None = None) -> Venue | None:
        n = slug(name)
        for v in self.venues:
            names = [v["name"], *v.get("aliases", [])]
            if any(slug(x) == n or slug(x) in n for x in names):
                if city and v.get("city") and slug(city) != slug(v["city"]) and slug(v["city"]) not in n:
                    continue
                return Venue(
                    name=v["name"], city=v["city"], province=v.get("province"),
                    lat=v.get("lat"), lon=v.get("lon"), address=v.get("address"),
                    kind=v.get("kind", "club"), capacity=v.get("capacity"),
                    socials=Socials(**(v.get("socials") or {})), verified=v.get("verified", False),
                )
        return None

    def is_preserata(self, venue: Venue) -> bool:
        """Pre-party venue (settings.yaml preserata_kinds, or `preserata:` on the venue in venues.yaml)."""
        n = slug(venue.name)
        for v in self.venues:
            if "preserata" in v and any(slug(x) == n for x in [v["name"], *v.get("aliases", [])]):
                return bool(v["preserata"])
        return venue.kind in (self.settings.get("preserata_kinds") or ["bar"])

    def enrich_venue(self, venue: Venue) -> Venue:
        """Fill gaps (coords, province, handles) from the registry and town table."""
        known = self.match_venue(venue.name, venue.city)
        if known:
            venue.name = known.name
            venue.city = venue.city or known.city
            venue.province = venue.province or known.province
            venue.lat = venue.lat if venue.lat is not None else known.lat
            venue.lon = venue.lon if venue.lon is not None else known.lon
            venue.kind = known.kind if venue.kind in (None, "", "other", "club") else venue.kind
            venue.capacity = venue.capacity or known.capacity
            venue.socials = venue.socials.merge(known.socials)
            venue.verified = known.verified
        town = self.towns.get(venue.city or "")
        if town:
            venue.province = venue.province or town.get("province")
            if venue.lat is None:
                venue.lat, venue.lon = town.get("lat"), town.get("lon")
        return venue
