"""Porta nel nuovo sistema quello che una persona ha già scritto a mano nella versione 1.

    python -m nightlife import-v1 archivio/database-v1 --city bergamo

Legge config/ della versione 1 (locali, promoter, generi, voti, nascosti, artisti, eventi a mano) e scrive
cities/<città>/conoscenze-importate.yaml. conoscenze.yaml resta quello scritto a mano e vince sempre. Per una città nuova non serve: il sistema
trova tutto da solo, le conoscenze sono solo un aiuto in più.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from .city import City
from .util import km_between, norm

TYPES = {"club", "bar", "live_club", "open_air", "arena"}


def _y(path: Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def _match(city: City, v: dict) -> dict | None:
    """Il locale della versione 1 nell'anagrafica trovata da sola (stesso profilo, stesso nome, o nome simile a meno di 1 km)."""
    h = (v.get("instagram") or (v.get("socials") or {}).get("instagram") or "").lower()
    if h and (m := city.venue_by_handle(h)):
        return m
    for n in [v["name"], *(v.get("aliases") or [])]:
        if m := city.venue_named(n, exact=True):
            return m
    m = city.venue_named(v["name"])
    if m and m.get("lat") is not None and v.get("lat") is not None and km_between(m["lat"], m["lon"], v["lat"], v["lon"]) <= 1.0:
        return m
    return None


def import_v1(city: City, old: Path) -> tuple[str, dict]:
    cfg = old / "config"
    venues = _y(cfg / "venues.yaml").get("venues", [])
    proms = _y(cfg / "promoters.yaml").get("promoters", [])
    genres = _y(cfg / "genres.yaml")
    rank = _y(cfg / "ranking.yaml")
    hidden = _y(cfg / "hidden.yaml").get("hide", [])
    watch = _y(cfg / "watchlist.yaml")
    manual = _y(old / "data" / "manual_events.yaml").get("events", [])
    sources = _y(cfg / "sources.yaml").get("sources", [])
    rv = rank.get("venues") or {}
    gv, ga, gp = genres.get("venues") or {}, genres.get("always") or {}, genres.get("promoters") or {}
    today = date.today().isoformat()
    stats = {"locali": 0, "uniti": 0, "nuovi": 0, "promoter": 0, "artisti": 0, "nascosti": 0, "eventi": 0}

    out_v = []
    for v in venues:
        name = v["name"]
        h = v.get("instagram") or (v.get("socials") or {}).get("instagram")
        m = _match(city, v)
        p: dict = {"name": m["name"] if m else name}
        aliases = sorted({a for a in [name, *(v.get("aliases") or [])] if norm(a) != norm(p["name"])})
        if aliases:
            p["aliases"] = aliases
        if h:
            p["instagram"] = h
        if v.get("kind") in TYPES:
            p["type"] = v["kind"]
        if not m:   # il sistema non l'ha ancora trovato: servono indirizzo e coordinate
            p.update({k: v[k] for k in ("address", "city", "lat", "lon") if v.get(k) is not None})
        f = str(v.get("fascia") or "")
        if f in ("serata", "preserata"):
            p["fascia"] = f
        elif f.startswith("sempre "):
            p["fascia"], p["fascia_fixed"] = f.split()[1], True
        r = rv.get(name)
        if isinstance(r, (int, float)):
            p["vote"] = r
        elif isinstance(r, dict):
            if r.get("score") is not None:
                p["vote"] = r["score"]
            if r.get("capacity") is not None:
                p["capacity"] = r["capacity"]
            if r.get("rooms"):
                p["rooms"] = {k: {"vote": x.get("score"), "capacity": x.get("capacity")} for k, x in r["rooms"].items()}
        if v.get("capacity") and "capacity" not in p:
            p["capacity"] = v["capacity"]
        if name in ga:
            p["genre"], p["genre_always"] = ga[name], True
        elif name in gv:
            p["genre"] = gv[name]
        if v.get("category") == "ballo_da_sala":
            p["category"] = "ballo da sala"
        if v.get("in_posts") is False:
            p["in_posts"], p["big"] = False, True
        if p.get("vote") is not None or p.get("in_posts") is False:
            p["watch"] = True
        out_v.append(p)
        stats["locali"] += 1
        stats["uniti" if m else "nuovi"] += 1

    out_p = []
    for pr in proms:
        p = {"name": pr["name"]}
        if pr.get("instagram"):
            p["instagram"] = pr["instagram"]
        if pr.get("party_name"):
            p["party_name"] = pr["party_name"]
        if pr.get("aliases"):
            p["aliases"] = pr["aliases"]
        if gp.get(pr["name"]):
            p["genre"] = gp[pr["name"]]
        vote = (rank.get("promoters") or {}).get(pr["name"])
        if vote is not None:
            p["vote"] = vote
        elif pr.get("relevance") == "low":
            p["vote"] = 3
        if pr.get("venues"):
            p["venues"] = pr["venues"]
        out_p.append(p)
    stats["promoter"] = len(out_p)

    known = set(city.artists)
    arts = [{"name": a, "level": "top"} for a in watch.get("dj_headliners") or []] + \
           [{"name": a, "level": "notable"} for a in watch.get("notable_artists") or []] + \
           [{"name": a, "level": "live"} for a in watch.get("crossover_live") or []]
    arts = [a for a in arts if norm(a["name"]) not in known]
    stats["artisti"] = len(arts)
    brands = [b for b in watch.get("national_brands") or [] if b not in city.brands]

    hide = [h for h in hidden]
    stats["nascosti"] = len(hide)
    evs = []
    for e in manual:
        s = str(e.get("start") or "")
        d = s[:10]
        if not d or d < today:
            continue
        evs.append({k: x for k, x in {"date": d, "start": s[11:16] or None, "venue": e.get("venue") if isinstance(e.get("venue"), str) else (e.get("venue") or {}).get("name"),
                                       "party": e.get("title"), "promoter": e.get("promoter"), "room": e.get("room"), "genre": ", ".join(e.get("genres") or []) or None}.items() if x})
    stats["eventi"] = len(evs)

    seeds = []
    for s in sources:
        if s.get("type") == "instagram":
            for a in s.get("accounts") or []:
                h = a if isinstance(a, str) else a.get("username")
                if h and h not in seeds:
                    seeds.append(h)

    block = {"seeds": seeds, "venues": out_v, "promoters": out_p}
    if arts:
        block["artists"] = arts
    if brands:
        block["brands"] = brands
    if hide:
        block["hide"] = hide
    if evs:
        block["events"] = evs
    return yaml.safe_dump(block, allow_unicode=True, sort_keys=False, width=200, default_flow_style=None), stats


def write(city: City, old: Path) -> str:
    """Rigenera cities/<città>/conoscenze-importate.yaml. conoscenze.yaml (scritto a mano) non si tocca e vince sempre."""
    body, stats = import_v1(city, old)
    head = ("# Generato da: python -m nightlife import-v1 " + str(old) + "  (non modificare a mano: si rigenera)\n"
            "# Le correzioni vanno in conoscenze.yaml, che vince su questo file.\n")
    (city.dir / "conoscenze-importate.yaml").write_text(head + body, encoding="utf-8")
    return ", ".join(f"{k}: {v}" for k, v in stats.items())
