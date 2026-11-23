"""Scoperta automatica dei locali di una città. Nessun dato di partenza: bastano centro e raggio.

1. OpenStreetMap (gratis, senza chiave): discoteche, locali da concerto, sale da ballo, bar con musica dal vivo.
2. Sito del locale: da lì si ricava il profilo Instagram.
3. Profili Instagram classificati come "locale" (taggati nei post di eventi): diventano locali, con indirizzo dalla biografia.
4. Nominatim (gratis): indirizzo -> coordinate, per calcolare la distanza dal centro.
5. Capienza: quella dichiarata, altrimenti una stima da tipo di locale e follower.
6. Google Maps (facoltativo, chiave GOOGLE_PLACES_KEY, una volta al mese, dentro la quota gratuita): i club che su OpenStreetMap mancano.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date

from .city import City, clean_name
from .util import fetch, km_between, norm, scale

log = logging.getLogger("nightlife.discover")
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
PHOTON = "https://photon.komoot.io/api/"
# scuole di ballo, palestre, sale per matrimoni e sale comunali: su OpenStreetMap stanno con i locali da ballo, ma non sono nightlife
NOT_NIGHTLIFE = re.compile(r"\b(?:bandistic\w*|filarmonica|corpo\s+musicale|quartet|quintet|organo|anziani|animation|animazione|coro|corale|sala\s*slot|slot|vlt|sexy|lap\s*dance|strip|ex\s+discoteca|s\.?r\.?l|school|scuola|academy|accademia|asd|a\.s\.d|ssd|palestra|studio danza|danza|wedding|matrimoni|sposi|oratorio|parrocchi|pro loco|sala civica|comunale|biblioteca)\b", re.I)
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


GOOGLE_TEXT = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_FIELDS = ("places.displayName,places.formattedAddress,places.location,places.types,places.primaryType,"
                 "places.businessStatus,places.websiteUri,places.userRatingCount,nextPageToken")
GOOGLE_TYPES = {"cultural_center": "live_club", "community_center": "live_club", "event_venue": "live_club", "performing_arts_theater": "live_club",
                "cafe": "bar", "coffee_shop": "bar", "cafeteria": "bar", "bakery": "bar", "dessert_shop": "bar", "confectionery": "bar",
                "night_club": "club", "dance_hall": "club", "concert_hall": "live_club", "live_music_venue": "live_club", "amphitheatre": "open_air",
                "bar": "bar", "pub": "bar", "cocktail_bar": "bar", "lounge_bar": "bar", "wine_bar": "bar", "bar_and_grill": "bar", "karaoke": "bar"}
GOOGLE_SKIP = {"wedding_venue", "banquet_hall", "dance_school", "gym", "school", "lodging", "hotel"}


def from_google(city: City) -> int:
    """Google Maps (Places API New, ricerca per testo nel riquadro della città). Una volta al mese, circa 20 richieste:
    resta nella quota gratuita. Senza chiave non fa niente."""
    key = os.environ.get("GOOGLE_PLACES_KEY")
    if not key:
        return -1
    import math
    c = city.c
    dlat = c["radius_km"] / 111.0
    dlon = c["radius_km"] / (111.0 * math.cos(math.radians(c["lat"])))
    rect = {"rectangle": {"low": {"latitude": c["lat"] - dlat, "longitude": c["lon"] - dlon},
                          "high": {"latitude": c["lat"] + dlat, "longitude": c["lon"] + dlon}}}
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": GOOGLE_FIELDS, "Content-Type": "application/json"}
    added = 0
    for q in city.settings["sources"].get("google", {}).get("queries") or []:
        token = None
        for _ in range(3):   # al massimo 60 risultati per ricerca
            body = {"textQuery": q, "languageCode": "it", "regionCode": c.get("country", "it").upper(), "locationRestriction": rect, "pageSize": 20}
            if token:
                body["pageToken"] = token
            res = fetch(GOOGLE_TEXT, method="POST", json=body, headers=headers, pause=0.5).json()
            for pl in res.get("places", []):
                name = (pl.get("displayName") or {}).get("text")
                loc = pl.get("location") or {}
                types = set(pl.get("types") or [])
                if not name or loc.get("latitude") is None or pl.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
                    continue
                if types & GOOGLE_SKIP or NOT_NIGHTLIFE.search(name) or WEDDING.search(name):
                    continue
                typ = GOOGLE_TYPES.get(pl.get("primaryType")) or next((GOOGLE_TYPES[t] for t in ("night_club", "concert_hall", "live_music_venue", "bar", "pub", "cultural_center", "cafe") if t in types), None)
                if not typ or (city.distance(loc["latitude"], loc["longitude"]) or 99) > c["radius_km"]:
                    continue
                site = pl.get("websiteUri") or ""
                m = IG_LINK.search(site)
                addr = re.sub(r",\s*(?:Italia|Italy)$", "", pl.get("formattedAddress") or "")
                added += upsert(city, {"name": clean_name(name), "address": addr or None, "lat": round(loc["latitude"], 5), "lon": round(loc["longitude"], 5),
                                       "type": typ, "instagram": m.group(1).lower() if m else None, "website": None if m else (site or None),
                                       "reviews": pl.get("userRatingCount")}, "google")
            token = res.get("nextPageToken")
            if not token:
                break
    return added


_google_budget = {"left": 15}   # ricerche per nome a ogni giro, al massimo


def google_place(city: City, name: str) -> dict | None:
    """Un locale nominato in un evento ma mai visto: lo cerca per nome su Google Maps vicino alla città.
    Tiene il risultato solo se il nome corrisponde davvero. Ogni nome si cerca una volta sola."""
    key = os.environ.get("GOOGLE_PLACES_KEY")
    cache = city.registry.setdefault("google_lookup", {})
    q = norm(name)
    if q in cache:
        return cache[q]
    if not key or len(q) < 3 or _google_budget["left"] <= 0:
        return None
    _google_budget["left"] -= 1
    c = city.c
    body = {"textQuery": f"{name}, {c['name']}", "languageCode": "it", "pageSize": 5,
            "locationBias": {"circle": {"center": {"latitude": c["lat"], "longitude": c["lon"]}, "radius": 50000.0}}}
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": GOOGLE_FIELDS.replace(",nextPageToken", ""), "Content-Type": "application/json"}
    try:
        res = fetch(GOOGLE_TEXT, method="POST", json=body, headers=headers, pause=0.5).json()
    except Exception as e:  # noqa: BLE001
        log.warning("google %s: %s", name, e)
        return None
    out = None
    want = set(q.split()) - {"bar", "club", "pub", "lounge", "il", "la", "lo", "the", "di", "del"}
    for pl in res.get("places", []):
        pname = (pl.get("displayName") or {}).get("text") or ""
        got = set(norm(pname).split())
        loc = pl.get("location") or {}
        if not want or loc.get("latitude") is None or pl.get("businessStatus", "OPERATIONAL") == "CLOSED_PERMANENTLY":
            continue
        if len(want & got) / len(want) < 0.6 and q not in norm(pname):   # nome diverso: meglio niente che il posto sbagliato
            continue
        types = set(pl.get("types") or [])
        typ = GOOGLE_TYPES.get(pl.get("primaryType")) or next((GOOGLE_TYPES[t] for t in GOOGLE_TYPES if t in types), "bar")
        site = pl.get("websiteUri") or ""
        m = IG_LINK.search(site)
        out = {"name": clean_name(pname), "address": re.sub(r",\s*(?:Italia|Italy)$", "", pl.get("formattedAddress") or "") or None,
               "lat": round(loc["latitude"], 5), "lon": round(loc["longitude"], 5), "type": typ, "instagram": m.group(1).lower() if m else None,
               "website": None if m else (site or None), "reviews": pl.get("userRatingCount"), "aliases": [name] if norm(pname) != q else []}
        break
    cache[q] = out
    return out


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


def _same_place(wanted: str, found: str | None) -> bool:
    """Il posto trovato sulla mappa ha davvero quel nome (almeno una parola non generica in comune)."""
    from .city import GENERIC
    skip = GENERIC | {"teatro", "spazio", "circolo", "centro", "sala", "piazza", "via", "locale"}
    a = {w for w in norm(wanted).split() if len(w) >= 3 and w not in skip}
    b = {w for w in norm(found or "").split() if len(w) >= 3}
    return bool(a & b) if a else False


def geocode(city: City, query: str, name: str | None = None) -> dict | None:
    """Indirizzo o nome di un posto -> coordinate. Photon (gratis), poi Nominatim. Il risultato resta in memoria."""
    cache = city.registry.setdefault("geocode", {})
    key = query + (" |nome" if name else "")
    if key in cache:
        return cache[key]
    out = None
    if _geo_fail["photon"] < 3:
        try:
            res = fetch(PHOTON, params={"q": query, "limit": 1, "lat": city.c["lat"], "lon": city.c["lon"], "lang": "default"}, pause=1.0, bot_ua=True).json()
            _geo_fail["photon"] = 0
            f = (res.get("features") or [None])[0]
            if f:
                lon, lat = f["geometry"]["coordinates"]
                pr = f.get("properties", {})
                if name and not _same_place(name, pr.get("name")):   # "Teatro Miela" non è il Teatro Donizetti
                    f = None
            if f:
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
            if res and name and not _same_place(name, res[0].get("name")):
                res = []
            if res:
                a = res[0].get("address", {})
                out = {"lat": round(float(res[0]["lat"]), 5), "lon": round(float(res[0]["lon"]), 5),
                       "city": a.get("city") or a.get("town") or a.get("village") or a.get("municipality"), "province": (a.get("ISO3166-2-lvl6") or "")[-2:] or None}
        except Exception as e:  # noqa: BLE001
            _geo_fail["nominatim"] += 1
            log.warning("nominatim %s: %s", query, e)
            return None
    if out is not None or _geo_fail["photon"] < 3:
        cache[key] = out
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
            place = geocode(city, f"{name}, {city.c['name']}", name=name)
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
            place = geocode(city, f"{a.get('name') or h}, {city.c['name']}", name=a.get("name") or h)
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
        hall = v.get("type") not in ("club", "bar") and not party   # un bar che fa anche feste private resta
        cinema = re.search(r"cinema|cineteca|multisala", f"{v.get('instagram') or ''} {bio}", re.I) and not party
        v["removed"] = bool(NOT_NIGHTLIFE.search(v["name"]) or cinema or (WEDDING.search(bio) and hall) or (v.get("type") == "live_club" and EVENT_HALL.search(v["name"]) and not party))
    last = city.registry.get("osm_checked")
    if city.settings["sources"]["osm"].get("enabled") and (force_osm or not last or (date.today() - date.fromisoformat(last)).days >= 7):
        try:
            n = from_osm(city)
            city.registry["osm_checked"] = date.today().isoformat()
            notes.append(f"OpenStreetMap: {n} locali nuovi, {len(city.registry['venues'])} in anagrafica.")
        except Exception as e:  # noqa: BLE001
            notes.append(f"OpenStreetMap saltato: {e}")
    lastg = city.registry.get("google_checked")
    gq = "|".join(city.settings["sources"].get("google", {}).get("queries") or [])
    changed = city.registry.get("google_queries") != gq   # ricerche nuove nelle impostazioni: si rifà subito
    if city.settings["sources"].get("google", {}).get("enabled") and (force_osm or changed or not lastg or (date.today() - date.fromisoformat(lastg)).days >= city.settings["sources"]["google"].get("every_days", 30)):
        try:
            n = from_google(city)
            if n >= 0:
                city.registry["google_checked"] = date.today().isoformat()
                city.registry["google_queries"] = gq
                notes.append(f"Google Maps: {n} locali nuovi.")
        except Exception as e:  # noqa: BLE001
            notes.append(f"Google Maps saltato: {str(e)[:150]}")
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
