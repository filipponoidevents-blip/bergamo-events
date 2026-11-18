"""Fonti web oltre a Instagram: siti di biglietti (Xceed, DICE), pagine con dati strutturati, articoli.

Ogni funzione restituisce eventi "grezzi" nello stesso formato del lettore di didascalie.
Una fonte che non risponde viene saltata: l'errore finisce nel report e il resto va avanti.
"""
from __future__ import annotations

import html as htmllib
import json
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .city import City, clean_name
from .reader import Reader, find_genre, find_type
from .util import fetch, norm

log = logging.getLogger("nightlife.web")


def night_of(dt: datetime) -> tuple[str, str]:
    """La sera a cui appartiene un orario: le 00:30 di domenica sono la serata di sabato."""
    d = dt.date() - timedelta(days=1) if dt.hour < 6 else dt.date()
    return d.isoformat(), dt.strftime("%H:%M")


def raw(city: City, *, date_, start, end=None, venue=None, venue_rec=None, party=None, guests=(), text="", source, url=None, price=None, address=None, conf=0.85) -> dict:
    v = venue_rec or city.venue_named(venue)
    tn = norm(f"{party or ''} {text}")
    prom = city.promoter_in_text(tn)
    return {"date": date_, "start": start, "end": end, "late_end": False, "venue": v["name"] if v else venue, "venue_known": bool(v), "address": address,
            "room": None, "party": party, "promoter": prom.get("instagram") if prom else None, "promoter_name": prom["name"] if prom else None,
            "guests": [{"name": g, "instagram": None, "level": city.artist_level(g), "cue": True, "followers": None} for g in guests],
            "genre": find_genre(f"{party or ''}\n{text}"), "type": find_type(f"{party or ''}\n{text}"), "entry": "a pagamento" if price else None, "price": price,
            "tickets": url, "handles": [], "confidence": conf, "source": source, "account": None, "url": url, "posted": None, "text": (text or party or "")[:600]}


# ---- Xceed: la pagina di un locale contiene i suoi eventi ---------------------------
XCEED_EVENT = re.compile(r'"name":"((?:[^"\\]|\\.){2,120})","slug":"([a-z0-9-]+)","startingTime":(\d{9,11}),"endingTime":(\d{9,11})')


def xceed(city: City) -> tuple[list[dict], list[str]]:
    tz = ZoneInfo(city.c["timezone"])
    out, notes, state = [], [], city.registry.setdefault("xceed", {})
    slug_city = norm(city.c["name"]).replace(" ", "-")
    for v in city.venues:
        urls = [u for u in v.get("tickets") or [] if "xceed.me" in u]
        key = norm(v["name"])
        if not urls and v.get("type") in ("club", "open_air") and (v.get("km") or 99) <= city.c["radius_km"]:
            if state.get(key) == "none":
                continue
            urls = [f"https://xceed.me/it/{slug_city}/club/{norm(v['name']).replace(' ', '-')}"]
        for u in urls[:1]:
            u = re.sub(r"xceed\.me/(?!(?:it|en|es)/)", "xceed.me/it/", u)
            try:
                page = fetch(u, pause=2).text
            except Exception as e:  # noqa: BLE001
                state[key] = "none" if "404" in str(e) else state.get(key)
                if "404" not in str(e):
                    notes.append(f"Xceed {v['name']}: {e}")
                continue
            state[key] = u
            text = page.replace('\\"', '"').replace("\\\\u0026", "&").replace("\\u0026", "&")
            seen = set()
            for name, slug, t0, t1 in XCEED_EVENT.findall(text):
                if slug in seen:
                    continue
                seen.add(slug)
                s, e = datetime.fromtimestamp(int(t0), tz), datetime.fromtimestamp(int(t1), tz)
                if s.date() < date.today() - timedelta(days=1):
                    continue
                d, st = night_of(s)
                out.append(raw(city, date_=d, start=st, end=e.strftime("%H:%M"), venue_rec=v, party=clean_name(name), source="xceed", url=u, text=name))
    return out, notes


# ---- DICE: l'elenco di una città (serve soprattutto per i grandi eventi fuori zona) -------
def dice(city: City) -> tuple[list[dict], list[str]]:
    out, notes = [], []
    for u in (city.settings["sources"].get("dice") or {}).get("pages") or []:
        try:
            page = fetch(u, pause=2).text
            m = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
            events = _find_lists(json.loads(m.group(1)), "events") if m else []
        except Exception as e:  # noqa: BLE001
            notes.append(f"DICE: {e}")
            continue
        for ev in events:
            try:
                s = datetime.fromisoformat((ev.get("dates") or {}).get("event_start_date") or ev.get("date"))
                ven = (ev.get("venues") or [{}])[0]
                d, st = night_of(s.astimezone(ZoneInfo(city.c["timezone"])))
                guests = [a["name"] for a in ((ev.get("summary_lineup") or {}).get("top_artists") or []) if a.get("name")]
                out.append(raw(city, date_=d, start=st, venue=ven.get("name"), party=ev.get("name"), guests=guests, source="dice", address=ven.get("address"),
                               url=f"https://dice.fm/event/{ev.get('perm_name')}" if ev.get("perm_name") else u, text=ev.get("name") or ""))
            except Exception:  # noqa: BLE001
                continue
    return out, notes


def _find_lists(node, key) -> list:
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and isinstance(v, list) and v and isinstance(v[0], dict) and "name" in v[0]:
                found += v
            else:
                found += _find_lists(v, key)
    elif isinstance(node, list):
        for v in node:
            found += _find_lists(v, key)
    return found


# ---- Pagine con dati strutturati schema.org (agende dei giornali, siti dei locali) ---------
LD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def _ld_events(page: str) -> list[dict]:
    out = []
    def walk(n):
        if isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict):
            t = n.get("@type")
            types = t if isinstance(t, list) else [t]
            if any(isinstance(x, str) and x.endswith("Event") for x in types) and n.get("startDate"):
                out.append(n)
            for k in ("@graph", "itemListElement", "item", "subEvent", "event"):
                if k in n:
                    walk(n[k])
    for block in LD.findall(page):
        try:
            walk(json.loads(htmllib.unescape(block).strip()))
        except Exception:  # noqa: BLE001
            continue
    return out


def _links(page: str, base: str, pattern: str) -> list[str]:
    from urllib.parse import urljoin
    rx = re.compile(pattern)
    seen = []
    for href in re.findall(r'href="([^"#]+)"', page):
        u = urljoin(base, href)
        if rx.search(u) and u not in seen:
            seen.append(u)
    return seen


def jsonld(city: City) -> tuple[list[dict], list[str]]:
    tz = ZoneInfo(city.c["timezone"])
    out, notes = [], []
    for spec in (city.settings["sources"].get("jsonld") or {}).get("pages") or []:
        urls = [spec["url"].format(date=date.today() + timedelta(days=i)) for i in range(spec.get("days", 1))] if "{date" in spec["url"] else [spec["url"]]
        pages, visited = [], set()
        for u in urls:
            try:
                page = fetch(u).text
            except Exception as e:  # noqa: BLE001
                notes.append(f"{u}: {e}")
                continue
            pages.append((u, page))
            if spec.get("follow"):
                for link in _links(page, u, spec["follow"]):
                    if link not in visited and len(visited) < spec.get("max_pages", 40):
                        visited.add(link)
                        try:
                            pages.append((link, fetch(link).text))
                        except Exception:  # noqa: BLE001
                            continue
        for u, page in pages:
            for ev in _ld_events(page):
                try:
                    s = datetime.fromisoformat(str(ev["startDate"]).replace("Z", "+00:00"))
                    s = s.astimezone(tz) if s.tzinfo else s.replace(tzinfo=tz)
                    e = datetime.fromisoformat(str(ev["endDate"]).replace("Z", "+00:00")) if ev.get("endDate") else None
                    if e and (e.date() - s.date()).days > 2:   # una rassegna di mesi non è una serata
                        continue
                    loc = ev.get("location") or {}
                    loc = loc[0] if isinstance(loc, list) and loc else loc
                    addr = loc.get("address") if isinstance(loc, dict) else None
                    addr_text = ", ".join(str(addr.get(k)) for k in ("streetAddress", "addressLocality") if addr.get(k)) if isinstance(addr, dict) else addr
                    perf = ev.get("performer") or []
                    perf = perf if isinstance(perf, list) else [perf]
                    offers = ev.get("offers") or {}
                    offers = offers[0] if isinstance(offers, list) and offers else offers
                    price = None
                    try:
                        price = int(float(offers.get("price") or offers.get("lowPrice"))) if isinstance(offers, dict) else None
                    except (TypeError, ValueError):
                        pass
                    has_time = "T" in str(ev["startDate"]) and s.strftime("%H:%M") != "00:00"
                    d, st = night_of(s) if has_time else (s.date().isoformat(), None)
                    out.append(raw(city, date_=d, start=st, end=e.astimezone(tz).strftime("%H:%M") if e and e.tzinfo and has_time else None,
                                   venue=clean_name(loc.get("name", "")) if isinstance(loc, dict) else None, party=clean_name(htmllib.unescape(ev.get("name", ""))),
                                   guests=[p.get("name") for p in perf if isinstance(p, dict) and p.get("name")], source="agenda", url=ev.get("url") or u,
                                   price=price or None, address=addr_text, text=htmllib.unescape(re.sub(r"<[^>]+>", " ", str(ev.get("description") or ""))), conf=0.8))
                except Exception:  # noqa: BLE001
                    continue
    return out, notes


# ---- Articoli di giornale: il testo si legge come una didascalia --------------------------
def articles(city: City, reader: Reader) -> tuple[list[dict], list[str]]:
    out, notes = [], []
    for spec in (city.settings["sources"].get("articles") or {}).get("pages") or []:
        try:
            listing = fetch(spec["url"]).text
        except Exception as e:  # noqa: BLE001
            notes.append(f"{spec['url']}: {e}")
            continue
        for link in _links(listing, spec["url"], spec["follow"])[: spec.get("max_pages", 20)]:
            try:
                page = fetch(link).text
            except Exception:  # noqa: BLE001
                continue
            m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', page) or re.search(r'property="article:published_time"\s+content="([^"]+)"', page)
            try:
                when = datetime.fromisoformat(m.group(1).replace("Z", "+00:00")) if m else datetime.now().astimezone()
            except ValueError:
                when = datetime.now().astimezone()
            if (datetime.now().astimezone() - when).days > 30:
                continue
            title = htmllib.unescape(re.sub(r"<[^>]+>", "", (re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S) or [None, ""])[1]))
            body = "\n".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", p)) for p in re.findall(r"<p[^>]*>(.*?)</p>", page, re.S))[:3000]
            for ev in reader.read(f"{title}\n{body}", when=when, source="giornale", url=link):
                if ev["venue_known"] and ev["confidence"] >= 0.6:   # un articolo conta solo se parla di un locale noto
                    ev["confidence"] = min(ev["confidence"], 0.75)
                    out.append(ev)
    return out, notes
