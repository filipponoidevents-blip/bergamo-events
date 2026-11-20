"""Dal testo grezzo al dato finito: unisce le fonti, completa i campi, decide serata/preserata e dà il voto."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import discover, websources
from .city import City, clean_name
from .instagram import Instagram
from .reader import Reader
from .util import contains, day_note, hhmm, norm, now_iso, scale

log = logging.getLogger("nightlife")
PRIORITY = {"manuale": 100, "instagram-locale": 80, "instagram-promoter": 70, "instagram": 60, "xceed": 60, "dice": 55, "agenda": 50, "giornale": 40}
WEEKDAYS_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
SECOND_ROOM_DEFAULT_VOTE = 6


def collect(city: City, online: bool = True) -> tuple[list[dict], list[str]]:
    """Tutti gli eventi grezzi da tutte le fonti. `online=False` rilegge solo quello che è già in memoria."""
    notes: list[str] = []
    reader = Reader(city)
    ig = Instagram(city)
    web_path = city.data / "web_raw.json"
    web = json.loads(web_path.read_text(encoding="utf-8")) if web_path.exists() else {}
    if online:
        notes += discover.run(city)
        ig.run()
        notes += ig.problems
        src = city.settings["sources"]
        for name, fn in [("xceed", lambda: websources.xceed(city)), ("dice", lambda: websources.dice(city)), ("jsonld", lambda: websources.jsonld(city)),
                         ("articles", lambda: websources.articles(city, reader))]:
            if not (src.get(name) or {}).get("enabled", name == "dice"):
                continue
            try:
                evs, problems = fn()
                notes += problems
                if evs or not problems:
                    web[name] = evs
                notes.append(f"{name}: {len(evs)} eventi letti.")
            except Exception as e:  # noqa: BLE001
                notes.append(f"{name} saltato: {e}")
        today = date.today().isoformat()
        web = {k: [e for e in v if e["date"] >= today] for k, v in web.items()}
        web_path.write_text(json.dumps(web, ensure_ascii=False, indent=1), encoding="utf-8")
    raws: list[dict] = [e for v in web.values() for e in v]
    hidden_posts = {str(h["post"]) for h in city.know.get("hide") or [] if h.get("post")}
    found = Counter()
    scouts = {h for h, a in city.registry["accounts"].items() if a.get("kind") == "scout"}
    for pid, post in ig.posts.items():
        if pid in hidden_posts or post["account"] in scouts:   # le pagine "scout" servono solo a scoprire profili
            continue
        evs = reader.read(post["caption"], when=datetime.fromisoformat(post["at"]), account=post["account"], url=post.get("permalink"))
        for e in evs:
            e["post"] = pid
            e["image"] = post.get("image")
            e["source"] = "instagram-locale" if city.venue_by_handle(post["account"]) else ("instagram-promoter" if city.promoter(post["account"]) else "instagram")
        found[post["account"]] += bool(evs)
        raws += evs
    for h, n in found.items():
        city.registry["accounts"].setdefault(h, {})["events_found"] = n
    for m in (city.know.get("events") or []):
        v = city.venue_named(m.get("venue"))
        raws.append({"date": str(m["date"]), "start": m.get("start"), "end": m.get("end"), "late_end": False, "venue": v["name"] if v else m.get("venue"), "venue_known": bool(v),
                     "address": m.get("address"), "room": m.get("room"), "party": m.get("party"), "promoter": None, "promoter_name": m.get("party"),
                     "guests": [{"name": g, "instagram": g if re.fullmatch(r"[a-z0-9._]+", g) else None, "level": city.artist_level(g), "cue": True, "followers": None} for g in m.get("guests") or []],
                     "genre": m.get("genre"), "type": m.get("type"), "entry": None, "price": m.get("price"), "tickets": m.get("url"), "handles": [], "confidence": 0.99,
                     "source": "manuale", "account": None, "url": m.get("url"), "posted": None, "text": m.get("notes") or "", "fascia": m.get("fascia")})
    return raws, notes


# ---- unione ------------------------------------------------------------------------
def _band(e: dict) -> str | None:
    s = hhmm(e.get("start"))
    return None if s is None else ("presto" if 6 * 60 <= s < 22 * 60 else "tardi")


def merge(raws: list[dict]) -> list[dict]:
    """Lo stesso evento visto da più fonti diventa una riga sola. Due sale nella stessa sera restano due serate."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for e in raws:
        if e.get("venue"):
            groups[(norm(e["venue"]), e["date"])].append(e)
    out = []
    for (_, _), items in groups.items():
        items.sort(key=lambda e: (-PRIORITY.get(e["source"], 30), bool(e.get("programme")), e.get("posted") or ""), reverse=False)
        slots: list[list[dict]] = []
        for e in sorted(items, key=lambda e: (e.get("room") is None, _band(e) is None)):
            room, band = e.get("room"), _band(e)
            target = None
            for s in slots:
                s_room = next((x["room"] for x in s if x.get("room")), None)
                s_band = next((_band(x) for x in s if _band(x)), None)
                same_room = (room == s_room) or (room is None and s_room in (None, "main room")) or (s_room is None and room == "main room")
                if same_room and (band is None or s_band is None or band == s_band):
                    target = s
                    break
            if target is None:
                slots.append([e])
            else:
                target.append(e)
        for s in slots:
            s.sort(key=lambda e: (-PRIORITY.get(e["source"], 30), bool(e.get("programme"))))
            out.append(_fuse(s))
    return out


def _fuse(items: list[dict]) -> dict:
    first = items[0]
    ev = {k: next((e[k] for e in items if e.get(k) not in (None, "", [])), None) for k in
          ("date", "start", "end", "venue", "address", "room", "genre", "type", "entry", "price", "tickets", "image", "fascia")}
    ev["venue_known"] = any(e["venue_known"] for e in items)
    ev["late_end"] = any(e.get("late_end") for e in items)
    with_prom = [e for e in items if e.get("promoter_name")]
    best = with_prom[0] if with_prom else next((e for e in items if e.get("party")), first)
    ev["party"], ev["promoter_name"], ev["promoter"] = best.get("party"), best.get("promoter_name"), best.get("promoter")
    guests, seen = [], set()
    for e in items:
        for g in e.get("guests") or []:
            if norm(g["name"]) not in seen:
                seen.add(norm(g["name"]))
                guests.append(g)
    ev["guests"] = guests
    ev["handles"] = sorted({h for e in items for h in e.get("handles") or []})
    ev["sources"] = sorted({e["source"] for e in items})
    ev["urls"] = list(dict.fromkeys(e["url"] for e in items if e.get("url")))
    ev["posts"] = sorted({e["post"] for e in items if e.get("post")})
    ev["accounts"] = sorted({e["account"] for e in items if e.get("account")})
    independent = len({e["source"].split("-")[0] + (e.get("account") or "") for e in items})
    ev["confidence"] = round(min(0.99, max(e["confidence"] for e in items) + 0.05 * (independent - 1)), 2)
    ev["posted"] = min((e["posted"] for e in items if e.get("posted")), default=None)
    ev["text"] = first.get("text") or ""
    ev["all_text"] = " \n".join(e.get("text") or "" for e in items)
    return ev


# ---- completamento, fascia, voto -----------------------------------------------------
def family_of(city: City, genre: str | None) -> str:
    fams = city.settings["genre_families"]
    g = norm((genre or "").split("/")[0])
    for fam, words in fams.items():
        if g and any(contains(g, w) or norm(w) == g for w in sorted(words, key=len, reverse=True)):
            return fam
    return list(fams)[-1]


def capacity_of(v: dict, room: str | None, d: date) -> int | None:
    cap = ((v.get("rooms") or {}).get(room or "") or {}).get("capacity") or v.get("capacity")
    if isinstance(cap, dict):
        cap = cap.get("summer" if 5 <= d.month <= 9 else "winter")
    return cap


def fascia_of(city: City, ev: dict, v: dict | None, big: bool) -> tuple[str, str]:
    f = city.settings["fascia"]
    if ev.get("fascia"):
        return ev["fascia"], "scelta a mano"
    if v and v.get("fascia_fixed"):
        return v.get("fascia", "preserata"), "locale sempre " + v.get("fascia", "preserata")
    if big or ev.get("type") == "festival":
        return "serata", "grande evento"
    s, e = hhmm(ev.get("start")), hhmm(ev.get("end"))
    late_start = s is not None and (s >= hhmm(f["serata_start"]) or s < 6 * 60)
    ends_late = ev.get("late_end") or (e is not None and hhmm(f["serata_end"]) <= e < 12 * 60)
    ends_early = e is not None and not ends_late
    if late_start and not ends_early:
        return "serata", f"inizia alle {ev['start']}"
    if s is not None and ends_late and s >= hhmm(f["early_start"]):
        return "serata", f"dalle {ev['start']} fino a tardi"
    if s is not None and s < hhmm(f["early_start"]) and not (ends_late and s >= 21 * 60):
        return "preserata", f"inizia alle {ev['start']}"
    if ends_early and e is not None:
        return "preserata", f"finisce alle {ev['end']}"
    if ends_late:
        return "serata", "finisce a tarda notte"
    if ev.get("type") in ("culture", "sport", "evento", "dinner show") and not ends_late:
        return "preserata", f"tipo di evento: {ev['type']}"
    if ev.get("type") in ("aperitivo", "quiz", "karaoke", "comedy", "live") and (v or {}).get("type") != "club":
        return "preserata", f"tipo di evento: {ev['type']}"
    if ev.get("entry") == "a pagamento" and (v or {}).get("fascia") != "preserata":
        return "serata", "ingresso a pagamento o in lista"
    if ev.get("entry") == "gratis" and (v or {}).get("fascia") != "serata":
        return "preserata", "ingresso gratuito"
    vt = (v or {}).get("fascia")
    if vt in ("serata", "preserata"):
        return vt, f"locale di solito {vt}"
    return ("serata", "discoteca") if (v or {}).get("type") in ("club", "arena") else ("preserata", "bar o locale diurno")


def build(city: City, raws: list[dict], online: bool = True) -> tuple[list[dict], list[dict]]:
    """Restituisce (eventi finiti, eventi scartati con il motivo)."""
    r = city.settings["ranking"]
    radius = city.c["radius_km"]
    hide = [h for h in city.know.get("hide") or [] if not h.get("post")]
    fixes = city.know.get("fix") or []
    learned = _learned(city)
    events, dropped = [], []
    for ev in merge(raws):
        d = date.fromisoformat(ev["date"])
        v = city.venue_named(ev["venue"], exact=True) or (city.venue_named(ev["venue"]) if ev["venue_known"] else None)
        if not v and online and (ev.get("address") or ev.get("venue")):
            v = _new_venue(city, ev)
        tn = norm(f"{ev.get('party') or ''} {ev['all_text']}")
        if any(all((k == "why") or (k == "text" and contains(tn, val)) or (k == "venue" and v and norm(val) == norm(v["name"])) or (k == "date" and str(val) == ev["date"])
                   for k, val in h.items()) for h in hide):
            dropped.append({**_slim(ev), "why": "nascosto in conoscenze.yaml"})
            continue
        for fx in fixes:
            if (fx.get("post") and str(fx["post"]) in ev["posts"]) or (fx.get("venue") and v and norm(fx["venue"]) == norm(v["name"]) and str(fx.get("date")) == ev["date"]
                                                                        and (not fx.get("room") or fx["room"] == ev.get("room"))):
                ev.update({k: val for k, val in fx.items() if k in ("party", "genre", "fascia", "start", "end", "room", "type", "headliner")})
                ev["fixed"] = True
        prom = city.promoter(ev.get("promoter")) or (city.promoter_in_text(norm(ev["party"])) if ev.get("party") else None)
        km = v.get("km") if v else None
        brand = city.brand_in(tn)
        for g in ev["guests"]:
            g["level"] = g.get("level") or city.artist_level(g["name"])
            g["followers"] = g.get("followers") or (city.followers(g["instagram"]) if g.get("instagram") else None)
            g["score"] = {"top": 10.0, "notable": 8.0, "live": 8.0}.get(g["level"]) or scale(g["followers"], r["followers_floor"], r["followers_top"])
        ev["guests"].sort(key=lambda g: (-g["score"], not g.get("cue")))
        top_guest = ev["guests"][0] if ev["guests"] else None
        in_zone = km is not None and km <= radius
        big = not in_zone and bool(brand or (v and (v.get("big") or (v.get("vote") or 0) >= 9)) or (top_guest and top_guest["level"] == "top"))
        if km is None and not big:
            in_zone = any(city.venue_by_handle(a) or city.promoter(a) for a in ev["accounts"])  # locale sconosciuto ma annunciato da un profilo della zona
            ev["confidence"] = round(ev["confidence"] - 0.15, 2)
        if not in_zone and not big:
            dropped.append({**_slim(ev), "why": f"fuori zona ({km} km) e non è un grande evento"})
            continue
        # genere: prima le scelte fatte a mano, poi il testo, poi quello che il promoter o il locale fanno di solito
        genre, gsrc = ev.get("genre"), "post"
        if ev.get("fixed") and ev.get("genre"):
            gsrc = "scelta a mano"
        elif v and v.get("genre_always") and v.get("genre"):
            genre, gsrc = v["genre"], "locale (sempre)"
        elif prom and prom.get("genre"):
            genre, gsrc = prom["genre"], "promoter (conoscenze)"
        elif not genre:
            for val, src in [((v or {}).get("genre"), "locale (conoscenze)"), (learned["promoter"].get(ev.get("promoter") or ""), "promoter (abitudine)"),
                             (learned["venue"].get(norm(v["name"])) if v else None, "locale (abitudine)")]:
                if val:
                    genre, gsrc = val, src
                    break
        fam = family_of(city, genre)
        typ = (v or {}).get("category") or ev.get("type") or ("dj set" if (v or {}).get("type") == "club" or prom or ev["guests"] else "evento")
        fascia, why = fascia_of(city, {**ev, "type": typ}, v, big)
        show_guest = top_guest and (top_guest["level"] or (fam != "Commerciale" and (top_guest.get("cue") or top_guest["score"] > 0)))
        headliner = ev.get("headliner") or (top_guest["name"] if show_guest else None)
        party = ev.get("party")
        if party and headliner and norm(party) == norm(headliner):
            party = None
        vfol = city.followers(v.get("instagram")) if v else None
        cap = capacity_of(v, ev.get("room"), d) if v else None
        room_vote = (((v or {}).get("rooms") or {}).get(ev.get("room") or "") or {}).get("vote")
        if room_vote is None and ev.get("room") and ev["room"] != "main room":
            room_vote = SECOND_ROOM_DEFAULT_VOTE
        venue_vote = (v.get("vote") if v and v.get("vote") is not None else (v or {}).get("auto_vote")) or 0
        pfol = (prom or {}).get("followers") or city.followers(ev.get("promoter"))
        parts = {
            "venue": round(venue_vote * (room_vote / 10 if room_vote else 1), 1),
            "capacity": scale(cap, r["capacity_floor"], r["capacity_top"]),
            "venue_followers": scale(vfol, r["followers_floor"], r["followers_top"]),
            "promoter": float(prom["vote"]) if prom and prom.get("vote") is not None else scale(pfol, r["followers_floor"], r["followers_top"]),
            "guest": top_guest["score"] if top_guest else 0.0,
            "distance": round(max(0.0, 10 * (1 - km / radius)), 1) if km is not None and in_zone else 0.0,
        }
        w = r[fascia]
        score = round(10 * sum(w[k] * parts[k] for k in w) / sum(w.values()), 1)
        nightlife = typ in city.settings["nightlife_types"]
        events.append({
            "id": "-".join(x for x in [ev["date"], norm(ev["venue"]).replace(" ", "_"), norm(ev.get("room") or "").replace(" ", "_"), "pre" if fascia == "preserata" and _band(ev) == "presto" else ""] if x),
            "date": ev["date"], "weekday": WEEKDAYS_IT[d.weekday()], "start": ev.get("start"), "end": ev.get("end"), "fascia": fascia, "fascia_why": why,
            "scope": "zona" if in_zone else "grande evento", "type": typ, "nightlife": nightlife,
            "party": party, "promoter_name": prom["name"] if prom else ev.get("promoter_name"), "promoter_instagram": (prom or {}).get("instagram") or ev.get("promoter"),
            "promoter_followers": pfol, "headliner": headliner,
            "guests": [{k: g.get(k) for k in ("name", "instagram", "followers", "level")} for g in ev["guests"][:8]],
            "genre": genre, "genre_source": gsrc if genre else None, "family": fam,
            "venue": v["name"] if v else ev["venue"], "venue_known": bool(v), "address": (v or {}).get("address") or ev.get("address"), "city": (v or {}).get("city"),
            "km": km, "venue_instagram": (v or {}).get("instagram"), "venue_type": (v or {}).get("type"), "venue_followers": vfol,
            "capacity": cap, "capacity_source": (v or {}).get("capacity_source") or ("conoscenze" if cap else None), "room": ev.get("room"),
            "entry": ev.get("entry"), "price": ev.get("price"), "tickets": ev.get("tickets"), "image": ev.get("image"),
            "score": score, "score_parts": {k: parts[k] for k in w}, "confidence": ev["confidence"],
            "sources": ev["sources"], "urls": ev["urls"][:4], "handles": ev["handles"], "posted": ev.get("posted"),
            "lead_days": (d - date.fromisoformat(ev["posted"][:10])).days if ev.get("posted") else None,
            "day_note": day_note(d), "in_posts": (v or {}).get("in_posts", True) is not False, "brand": brand,
        })
    _competition(events)
    return events, dropped


def _slim(ev: dict) -> dict:
    return {k: ev.get(k) for k in ("date", "venue", "party", "sources", "urls", "confidence")}


def _new_venue(city: City, ev: dict) -> dict | None:
    """Un locale mai visto: se ha un indirizzo o un nome trovabile sulla mappa entra in anagrafica da solo."""
    place = None
    for q in [f"{ev['address']}" if ev.get("address") else None, f"{ev['venue']}, {city.c['name']}" if ev.get("venue") else None]:
        if q:
            place = discover.geocode(city, q if "," in q else f"{q}, {city.c['name']}")
            if place:
                break
    if not place or not ev.get("venue") or len(norm(ev["venue"])) < 3:
        return None
    discover.upsert(city, {"name": clean_name(ev["venue"]), "address": ev.get("address"), "type": "bar", **place}, "eventi")
    discover.estimate(city)
    city._build()
    return city.venue_named(ev["venue"], exact=True)


def _learned(city: City) -> dict:
    """Il genere che un promoter o un locale fa di solito, imparato dagli eventi già raccolti."""
    path = city.data / "events.json"
    stored = json.loads(path.read_text(encoding="utf-8")).values() if path.exists() else []
    by = {"promoter": defaultdict(Counter), "venue": defaultdict(Counter)}
    for e in stored:
        if e.get("genre") and e.get("genre_source") in ("post", "scelta a mano"):
            if e.get("promoter_instagram"):
                by["promoter"][e["promoter_instagram"]][e["genre"]] += 1
            by["venue"][norm(e["venue"])][e["genre"]] += 1
    return {k: {name: c.most_common(1)[0][0] for name, c in d.items() if c.most_common(1)[0][1] >= 2} for k, d in by.items()}


def _competition(events: list[dict]) -> None:
    """Per ogni sera: posizione in classifica e concorrenza. È quello che serve a un promoter per scegliere la data."""
    nights = defaultdict(list)
    for e in events:
        if e["scope"] == "zona" and e["nightlife"]:
            nights[(e["date"], e["fascia"])].append(e)
    for group in nights.values():
        group.sort(key=lambda e: -e["score"])
        total_cap = sum(e["capacity"] or 0 for e in group)
        for i, e in enumerate(group):
            rivals = [x for x in group if x is not e]
            e["rank_in_night"] = i + 1
            e["competition"] = {"events_same_night": len(rivals), "same_family": sum(x["family"] == e["family"] for x in rivals),
                                "capacity_same_night": total_cap - (e["capacity"] or 0),
                                "strongest_rival": f"{rivals[0]['party'] or rivals[0]['headliner'] or rivals[0]['venue']} @ {rivals[0]['venue']}" if rivals else None}


# ---- memoria e stato ---------------------------------------------------------------
def run(city: City, online: bool = True) -> dict:
    raws, notes = collect(city, online)
    events, dropped = build(city, raws, online)
    today = date.today().isoformat()
    path = city.data / "events.json"
    stored = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    keep_from = (date.today() - timedelta(days=city.settings["publish"]["keep_past_days"])).isoformat()
    result = {k: v for k, v in stored.items() if keep_from <= v["date"] < today}   # il passato non si tocca più
    for e in events:
        if e["date"] < today:
            continue
        old = stored.get(e["id"], {})
        e["first_seen"] = old.get("first_seen") or now_iso()[:10]
        e["status"] = "pubblicato" if e["confidence"] >= city.settings["publish"]["min_confidence"] else "da controllare"
        result[e["id"]] = e
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    _update_registry(city, [e for e in result.values() if e["date"] >= today])
    city.save()
    return {"events": result, "dropped": dropped, "notes": notes}


def _update_registry(city: City, events: list[dict]) -> None:
    """Chi viene taggato negli eventi entra nell'elenco dei profili: così promoter e DJ nuovi si scoprono da soli."""
    accounts = city.registry["accounts"]
    mentions = Counter()
    for e in events:
        for h in set(e["handles"]) | {g["instagram"] for g in e["guests"] if g.get("instagram")} | ({e["promoter_instagram"]} if e.get("promoter_instagram") else set()):
            mentions[h] += 1
    for h, n in mentions.items():
        a = accounts.setdefault(h, {})
        a["mentions"] = n
        if a.get("kind") == "promoter" and n >= city.settings["instagram"]["auto_watch_after"] and a.get("readable") is not False:
            a["watch"] = True
