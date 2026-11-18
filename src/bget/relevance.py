"""Decide whether an event goes on the local calendar, becomes an alert, or is dropped."""
from __future__ import annotations

import math

from .config import Config
from .models import Category, Event, Scope


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _in(names: list[str], text: str) -> list[str]:
    t = text.lower()
    return [n for n in names if n.lower() in t]


def score_event(ev: Event, cfg: Config) -> Event:
    s = cfg.settings
    home = s["home"]
    th = s["thresholds"]
    reasons: list[str] = []
    score = 0.0

    # --- distance -------------------------------------------------------
    if ev.venue.lat is not None and ev.venue.lon is not None:
        ev.distance_km = round(haversine_km(home["lat"], home["lon"], ev.venue.lat, ev.venue.lon), 1)
    in_radius = ev.distance_km is not None and ev.distance_km <= home["radius_km"]

    # --- genre ----------------------------------------------------------
    haystack = " ".join([
        ev.title, ev.description, " ".join(ev.genres),
        " ".join(g for a in ev.artists for g in a.genres),
    ]).lower()
    best_genre, genre_score = None, 0.0
    for g, w in s["genres"].items():
        if g.lower() in haystack and w > genre_score:
            best_genre, genre_score = g, w
    if best_genre:
        score += genre_score
        reasons.append(f"genre:{best_genre}")

    if any(x.startswith(("ig:", "press:")) for x in ev.sources):
        # only DJ/club nights reach the store from Instagram, approved or high-confidence:
        # that alone is enough for the local calendar
        score += 2
        reasons.append("instagram-club-night")

    kind_bonus = s.get("venue_kind_bonus", {}).get(ev.venue.kind, 0)
    if kind_bonus:
        score += kind_bonus
        reasons.append(f"venue:{ev.venue.kind}")

    # --- watchlists -----------------------------------------------------
    wl = cfg.watchlist
    names_text = " | ".join([ev.title, *[a.name for a in ev.artists]])
    dj_hits = _in(wl.get("dj_headliners", []), names_text)
    live_hits = _in(wl.get("crossover_live", []), names_text)
    brand_hits = _in(wl.get("national_brands", []), " ".join([ev.title, ev.description, ev.promoter_name or ""]))
    if dj_hits:
        score += 4
        reasons.append("headliner:" + ",".join(dj_hits))
    if live_hits:
        score += 4
        reasons.append("crossover:" + ",".join(live_hits))
    if brand_hits:
        score += 5
        reasons.append("brand:" + ",".join(brand_hits))
    if ev.category == Category.FESTIVAL:
        score += 2
        reasons.append("festival")
    if (ev.venue.capacity or 0) >= 5000:
        score += 1.5
        reasons.append("big-venue")

    # --- concert rule ---------------------------------------------------
    # venues.yaml `calendar_also: [live_music]`: that venue's live nights count like club nights (NXT, Filippo)
    also = next((v.get("calendar_also") or [] for v in cfg.venues if v.get("name") == ev.venue.name), [])
    if ev.category == Category.CONCERT and "live_music" in also and in_radius:
        ev.score, ev.scope, ev.reasons = score, Scope.LOCAL, reasons + ["venue-live-nights"]
        return ev
    if ev.category == Category.CONCERT and th.get("concert_requires_watchlist") and not live_hits:
        ev.score, ev.scope, ev.reasons = score, Scope.IGNORE, reasons + ["concert-not-crossover"]
        return ev

    # --- scope ----------------------------------------------------------
    ev.alert_level = None
    if in_radius and score >= th["local_min_score"]:
        ev.scope = Scope.LOCAL
        # a local event can also be big news
        if brand_hits or dj_hits or live_hits:
            ev.alert_level = "national" if brand_hits else "regional"
    elif not in_radius and (score >= th["alert_min_score"] or brand_hits or dj_hits or live_hits):
        allowed = ev.venue.province in s.get("alert_provinces", [])
        if brand_hits or allowed:
            ev.scope = Scope.ALERT
            ev.alert_level = "national" if brand_hits else "regional"
        else:
            ev.scope = Scope.IGNORE
            reasons.append("outside-alert-provinces")
    else:
        ev.scope = Scope.IGNORE
        if ev.distance_km is None:
            reasons.append("no-location")

    ev.score, ev.reasons = round(score, 2), reasons
    return ev
