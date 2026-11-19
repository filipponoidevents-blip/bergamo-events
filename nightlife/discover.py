"""Scoperta automatica dei locali di una città. Nessun dato di partenza: bastano centro e raggio.

1. OpenStreetMap (gratis, senza chiave): discoteche, locali da concerto, sale da ballo, bar con musica dal vivo.
2. Sito del locale: da lì si ricava il profilo Instagram.
3. Profili Instagram classificati come "locale" (taggati nei post di eventi): diventano locali, con indirizzo dalla biografia.
4. Nominatim (gratis): indirizzo -> coordinate, per calcolare la distanza dal centro.
5. Capienza: quella dichiarata, altrimenti una stima da tipo di locale e follower.
"""
from __future__ import annotations

import logging
import re
from datetime import date

from .city import City, clean_name
from .util import fetch, km_between, norm, scale

log = logging.getLogger("nightlife.discover")
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
PHOTON = "https://photon.komoot.io/api/"
# scuole di ballo, palestre, sale per matrimoni e sale comunali: su OpenStreetMap stanno con i locali da ballo, ma non sono nightlife
NOT_NIGHTLIFE = re.compile(r"\b(?:school|scuola|academy|accademia|asd|a\.s\.d|ssd|palestra|studio danza|danza|wedding|matrimoni|sposi|oratorio|parrocchi|pro loco|sala civica|comunale|biblioteca)\b", re.I)
WEDDING = re.compile(r"\b(?:wedding|matrimoni\w*|sposi|ricevimenti|banqueting|catering|location per eventi|eventi privati|cerimonie)\b", re.I)
EVENT_HALL = re.compile(r"\b(?:villa|tenuta|castello|cascina|palazzo|agriturismo|ristorante|palafeste|area feste|sala|centro|corte|locanda|resort|hotel|relais)\b", re.I)
NOMINATIM = "https://nominatim.openstreetmap.org/search"
BASE_CAPACITY = {"club": 500, "live_club": 400, "open_air": 1000, "arena": 3000, "bar": 80}
IG_LINK = re.compile(r"instagram\.com/(?!p/|reel/|explore/|accounts/|stories/)([A-Za-z0-9_.]{3,30})")
ADDRESS = re.compile(r"\b((?:via|viale|piazza|piazzale|largo|corso|strada|localit[aà])\s+[^\n,|•·]{3,40}(?:,?\s*\d{1,4}[a-z]?)?)(?:[\s,–-]+(?:\d{5}\s+)?([A-Z][A-Za-zÀ-ú' ]{2,30}))?", re.I)


def osm_type(tags: dict) -> str:
    a = tags.get("amenity")
    if a == "nightclub" or tags.get("leisure") == "dance":
        return "club"
    if a in ("music_venue", "events_venue", "theatre", "arts_centre") or tags.get("club") == "music":
        return "live_club"
    return "bar"


def from_osm(city: City) -> int:
    c = city.c
    import math
    dlat = c["radius_km"] / 111.0
    dlon = c["radius_km"] / (111.0 * math.cos(math.radians(c["lat"])))
    bbox = f"{c['lat'] - dlat:.4f},{c['lon'] - dlon:.4f},{c['lat'] + dlat:.4f},{c['lon'] + dlon:.4f}"
    # riquadro fisso (veloce) e filtri semplici: la distanza vera si calcola dopo
    q = f"""[out:json][timeout:60][bbox:{bbox}];(
      nwr["amenity"~"^(nightclub|music_venue|events_venue)$"];
      nwr["leisure"="dance"];
      nwr["amenity"~"^(bar|pub|biergarten)$"]["live_music"="yes"];
    );out center tags;"""
    data = None
    for url in OVERPASS:
        try:
            data = fetch(url, method="POST", data={"data": q}, timeout=90, bot_ua=True).json()
            break
        except Exception as e:  # noqa: BLE001
            log.warning("OpenStreetMap %s: %s", url, e)
    if data is None:
        raise RuntimeError("OpenStreetMap non raggiungibile")
    added = 0
    for el in data.get("elements", []):
        t = el.get("tags", {})
        name = t.get("name")
        lat, lon = el.get("lat") or (el.get("center") or {}).get("lat"), el.get("lon") or (el.get("center") or {}).get("lon")
        if not name or lat is None or t.get("disused:amenity") or t.get("abandoned"):
            continue
        if (city.distance(lat, lon) or 99) > c["radius_km"]:
            continue
        if NOT_NIGHTLIFE.search(name) or (t.get("amenity") == "events_venue" and EVENT_HALL.search(name)):
            continue
        ig = t.get("contact:instagram") or t.get("instagram") or ""
        m = IG_LINK.search(ig)
        handle = (m.group(1) if m else ig.strip().lstrip("@").strip("/")).lower() or None
        street = " ".join(x for x in [t.get("addr:street"), t.get("addr:housenumber")] if x)
        rec = {"name": name, "address": ", ".join(x for x in [street, t.get("addr:postcode"), t.get("addr:city")] if x) or None, "city": t.get("addr:city"),
               "lat": round(lat, 5), "lon": round(lon, 5), "type": osm_type(t), "instagram": handle,
               "website": t.get("website") or t.get("contact:website"), "capacity": int(t["capacity"]) if str(t.get("capacity", "")).isdigit() else None,
               "capacity_source": "dichiarata" if str(t.get("capacity", "")).isdigit() else None, "osm": f"{el['type']}/{el['id']}"}
        added += upsert(city, rec, "osm")
    return added


def upsert(city: City, rec: dict, source: str) -> int:
    """Aggiunge un locale o completa quello che c'è già (non sovrascrive mai un dato esistente)."""
    venues = city.registry["venues"]
    key = norm(rec["name"])
    cur = venues.get(key) or next((v for v in venues.values() if rec.get("instagram") and v.get("instagram") == rec["instagram"]), None)
    if cur is None and rec.get("lat") is not None:  # stesso posto, nome scritto in modo diverso
        cur = next((v for v in venues.values() if v.get("lat") is not None and km_between(v["lat"], v["lon"], rec["lat"], rec["lon"]) < 0.08
                    and (norm(v["name"]) in key or key in norm(v["name"]))), None)
    new = cur is None
    if new:
        cur = venues[key] = {"name": rec["name"], "sources": [], "first_seen": date.today().isoformat()}
    elif norm(rec["name"]) != norm(cur["name"]) and rec["name"] not in cur.setdefault("aliases", []):
        cur["aliases"].append(rec["name"])
    for k, v in rec.items():
        if v not in (None, "", []) and cur.get(k) in (None, "", []):
            cur[k] = v
    if source not in cur["sources"]:
        cur["sources"].append(source)
    if cur.get("lat") is not None:
        cur["km"] = city.distance(cur["lat"], cur["lon"])
    return int(new)


_geo_fail = {"photon": 0, "nominatim": 0}


def geocode(city: City, query: str) -> dict | None:
    """Indirizzo o nome di un posto -> coordinate. Photon (gratis), poi Nominatim. Il risultato resta in memoria."""
    cache = city.registry.setdefault("geocode", {})
    if query in cache:
        return cache[query]
    out = None
    if _geo_fail["photon"] < 3:
        try:
            res = fetch(PHOTON, params={"q": query, "limit": 1, "lat": city.c["lat"], "lon": city.c["lon"], "lang": "default"}, pause=1.0, bot_ua=True).json()
            _geo_fail["photon"] = 0
            f = (res.get("features") or [None])[0]
            if f:
                lon, lat = f["geometry"]["coordinates"]
                pr = f.get("properties", {})
                out = {"lat": round(lat, 5), "lon": round(lon, 5), "city": pr.get("city") or pr.get("town") or pr.get("village") or pr.get("county"),
                       "province": None}
        except Exception as e:  # noqa: BLE001
            _geo_fail["photon"] += 1
            log.warning("photon %s: %s", query, e)
    if out is None and _geo_fail["nominatim"] < 3:
        try:
            res = fetch(NOMINATIM, params={"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1, "countrycodes": city.c.get("country", "it")},
                        pause=1.2, bot_ua=True).json()
            _geo_fail["nominatim"] = 0
            if res:
                a = res[0].get("address", {})
                out = {"lat": round(float(res[0]["lat"]), 5), "lon": round(float(res[0]["lon"]), 5),
                       "city": a.get("city") or a.get("town") or a.get("village") or a.get("municipality"), "province": (a.get("ISO3166-2-lvl6") or "")[-2:] or None}
        except Exception as e:  # noqa: BLE001
            _geo_fail["nominatim"] += 1
            log.warning("nominatim %s: %s", query, e)
            return None
    if out is not None or _geo_fail["photon"] < 3:
        cache[query] = out
    return out


XCEED_CLUB = re.compile(r'/(?:it|en)/([a-z0-9-]+)/(?:club|venue)/([a-z0-9-]+)')


def from_xceed(city: City) -> int:
    """La pagina della città su Xceed elenca i club che vendono lì: nome e link entrano in anagrafica."""
    slug = norm(city.c["name"]).replace(" ", "-")
    added = 0
    for url in [f"https://xceed.me/it/{slug}", f"https://xceed.me/it/{slug}/clubs", f"https://xceed.me/it/{slug}/venues"]:
        try:
            page = fetch(url, pause=2).text
        except Exception:  # noqa: BLE001
            continue
        for c_slug, v_slug in set(XCEED_CLUB.findall(page)):
            if c_slug != slug:
                continue
            name = v_slug.replace("-", " ").title()
            link = f"https://xceed.me/it/{slug}/club/{v_slug}"
            known = city.venue_named(name)
            if known:
                rec = city.registry["venues"].get(norm(known["name"]))
                if rec is not None and link not in (rec.get("tickets") or []):
                    rec.setdefault("tickets", []).append(link)
                continue
            place = geocode(city, f"{name}, {city.c['name']}")
            if place and (city.distance(place["lat"], place["lon"]) or 99) <= city.c["radius_km"] * 1.5:
                added += upsert(city, {"name": name, "type": "club", "tickets": [link], **place}, "xceed")
    return added


def from_accounts(city: City, limit: int = 15) -> int:
    """Un profilo Instagram che è un locale (bar, club) entra in anagrafica, con l'indirizzo scritto in biografia."""
    added = done = 0
    for h, a in city.registry["accounts"].items():
        if a.get("kind") != "venue" or a.get("venue_done") or city.venue_by_handle(h) or done >= limit:
            continue
        done += 1
        a["venue_done"] = True
        bio = a.get("bio") or ""
        m = ADDRESS.search(bio)
        place = None
        if m:
            place = geocode(city, f"{m.group(1)}, {m.group(2) or city.c['name']}")
        if not place:
            place = geocode(city, f"{a.get('name') or h}, {city.c['name']}")
        if not place:
            continue
        km = city.distance(place["lat"], place["lon"])
        if km is None or km > city.c["radius_km"] * 4:
            continue
        text = f"{a.get('name')} {bio}".lower()
        typ = "club" if re.search(r"\bclub\b|discoteca|disco\b", text) and not re.search(r"lounge|cocktail|bar\b|caf", text) else ("live_club" if re.search(r"live\s*(?:music|club)|concerti|teatro", text) else "bar")
        added += upsert(city, {"name": a.get("name") or h, "address": m.group(0).strip() if m else None, "instagram": h, "website": a.get("website"), "type": typ, **place}, "instagram")
    return added


def from_websites(city: City, limit: int = 8) -> int:
    """Il profilo Instagram di un locale, letto dal suo sito."""
    found = done = 0
    for v in city.registry["venues"].values():
        if v.get("instagram") or not v.get("website") or v.get("website_checked") or done >= limit:
            continue
        done += 1
        v["website_checked"] = True
        try:
            html = fetch(v["website"] if v["website"].startswith("http") else "https://" + v["website"], timeout=20).text
        except Exception:  # noqa: BLE001
            continue
        hits = [h.lower().strip(".") for h in IG_LINK.findall(html)]
        if hits:
            v["instagram"] = max(set(hits), key=hits.count)
            found += 1
    return found


def from_search(city: City, limit: int = 10) -> int:
    """Profilo Instagram di un locale cercato sul web (DuckDuckGo, gratis): "<nome> <comune> instagram"."""
    found = done = 0
    order = sorted(city.registry["venues"].values(), key=lambda v: ({"club": 0, "live_club": 1, "open_air": 1}.get(v.get("type"), 2), v.get("km") or 99))
    for v in order:
        if v.get("instagram") or v.get("search_checked") or done >= limit:
            continue
        done += 1
        v["search_checked"] = date.today().isoformat()
        q = f"{v['name']} {v.get('city') or city.c['name']} instagram"
        try:
            html = fetch("https://html.duckduckgo.com/html/", method="POST", data={"q": q}, pause=3).text
        except Exception as e:  # noqa: BLE001
            log.warning("ricerca %s: %s", q, e)
            break
        words = [w for w in norm(v["name"]).split() if len(w) >= 3 and w not in ("club", "bar", "the", "disco", "discoteca", "lounge", "cafe", "caffe")]
        for h in IG_LINK.findall(html.replace("%2F", "/")):
            h = h.lower().strip(".")
            if words and any(w in h.replace(".", "").replace("_", "") for w in words):
                v["instagram"] = h
                found += 1
                break
    return found


def estimate(city: City) -> None:
    """Capienza stimata e voto del locale, quando nessuno li ha scritti. Il voto è 0-10."""
    r = city.settings["ranking"]
    for v in city.registry["venues"].values():
        fol = city.followers(v.get("instagram"))
        typ = v.get("type") or "bar"
        if not v.get("capacity") or v.get("capacity_source") == "stima":
            factor = min(2.5, max(0.6, (fol / 5000) ** 0.5)) if fol else 1.0
            v["capacity"] = int(round(BASE_CAPACITY.get(typ, 80) * factor / 50) * 50) or 50
            v["capacity_source"] = "stima"
        v["followers"] = fol
        cap = max(v["capacity"].values()) if isinstance(v["capacity"], dict) else v["capacity"]
        v["auto_vote"] = round(0.4 * r["default_venue_vote"].get(typ, 3) + 0.3 * scale(fol, r["followers_floor"], r["followers_top"])
                               + 0.3 * scale(cap, r["capacity_floor"], r["capacity_top"]), 1)


def run(city: City, force_osm: bool = False) -> list[str]:
    notes = []
    accounts = city.registry["accounts"]
    for v in city.registry["venues"].values():   # pulizia: quello che i filtri di oggi escluderebbero
        if v.get("vote") or v.get("manual"):
            continue
        bio = (accounts.get(v.get("instagram") or "", {}).get("bio") or "")
        party = re.search(r"\b(?:club|discoteca|disco|live\s*music|concerti|dj|party|serate)\b", bio, re.I)
        if NOT_NIGHTLIFE.search(v["name"]) or WEDDING.search(bio) or (v.get("type") == "live_club" and EVENT_HALL.search(v["name"]) and not party):
            v["removed"] = True
    last = city.registry.get("osm_checked")
    if city.settings["sources"]["osm"].get("enabled") and (force_osm or not last or (date.today() - date.fromisoformat(last)).days >= 7):
        try:
            n = from_osm(city)
            city.registry["osm_checked"] = date.today().isoformat()
            notes.append(f"OpenStreetMap: {n} locali nuovi, {len(city.registry['venues'])} in anagrafica.")
        except Exception as e:  # noqa: BLE001
            notes.append(f"OpenStreetMap saltato: {e}")
    if city.settings["sources"]["xceed"].get("enabled"):
        n = from_xceed(city)
        if n:
            notes.append(f"Xceed: {n} club nuovi.")
    n = from_websites(city)
    if n:
        notes.append(f"Trovati {n} profili Instagram dai siti dei locali.")
    n = from_search(city)
    if n:
        notes.append(f"Trovati {n} profili Instagram cercando i locali sul web.")
    n = from_accounts(city)
    if n:
        notes.append(f"{n} locali nuovi scoperti da Instagram.")
    estimate(city)
    city.save()
    return notes
