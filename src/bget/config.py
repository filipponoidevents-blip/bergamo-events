from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import Socials, Venue, slug

ROOT = Path(__file__).resolve().parents[2]
CITIES_DIR = ROOT / "cities"
# Files a city may leave out: then the repo-wide copy in config/ is used.
# settings.yaml is merged: the city's file only needs what differs (home, provinces, labels...).
SHARED = ("watchlist.yaml",)


def city_root(city: str | None) -> Path:
    """Bergamo (the default city) lives at the repo root; every other city in cities/<name>/,
    with the same config/, data/ and out/ folders inside."""
    if not city or city.lower() == "bergamo":
        return ROOT
    root = CITIES_DIR / city.lower()
    if not (root / "config").is_dir():
        raise SystemExit(f"unknown city {city!r}: no folder {root / 'config'}")
    return root


def all_cities() -> list[str]:
    extra = sorted(p.name for p in CITIES_DIR.iterdir() if (p / "config").is_dir()) if CITIES_DIR.is_dir() else []
    return ["bergamo", *extra]


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


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
            path = root / "config" / name
            if not path.exists() and name in SHARED:
                path = ROOT / "config" / name
            return yaml.safe_load(path.read_text()) or {}
        v = y("venues.yaml")
        settings = y("settings.yaml")
        if root != ROOT:   # a city only writes what differs from the main settings
            settings = _merge(yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()) or {}, settings)
        return cls(
            settings=settings,
            sources=y("sources.yaml").get("sources", []),
            watchlist={k: list(vs or []) for k, vs in y("watchlist.yaml").items()},
            venues=v.get("venues", []),
            towns=v.get("towns", {}),
            promoters=(y("promoters.yaml").get("promoters", []) if (root / "config" / "promoters.yaml").exists() else []),
            genre_overrides=(y("genres.yaml") if (root / "config" / "genres.yaml").exists() else {}),
            root=root,
        )

    # ---- per-city labels (feeds, calendar names, posts) ------------------
    def label(self, key: str) -> str:
        """City labels from settings `city:`. Defaults are Bergamo's, so its feed names never change."""
        home = self.settings.get("home", {}).get("name", "Bergamo")
        c = self.settings.get("city", {})
        town = slug(home).replace("-", "")
        pre = "" if self.root == ROOT else town + "_"   # other cities' feeds sit next to Bergamo's
        defaults = {
            "name": home,
            "brand": f"{home} Nights",                       # calendar names
            "area": f"{home.upper()} E DINTORNI",            # post graphics
            "handle": "@serate" + town,                      # tracker account shown on posts
            "feed_local": town + ".ics",
            "feed_alerts": pre + "alerts.ics",
            "feed_all": pre + "all.ics",
            "feed_other": pre + "other.ics",
        }
        return str(c.get(key) or defaults[key])

    def hashtags(self) -> list[str]:
        c = self.settings.get("city", {})
        town = slug(self.label("name")).replace("-", "")
        return list(c.get("hashtags") or [f"#{town}", f"#{town}nightlife", "#techno", "#housemusic", "#techhouse", "#clubbing"])

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
