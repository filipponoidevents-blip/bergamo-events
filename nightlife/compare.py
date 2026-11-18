"""Verifica: quanto della lista fatta a mano (versione 1) il sistema ha trovato da solo."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .city import City
from .util import norm


def compare(city: City, old: Path) -> str:
    cfg = old / "config"
    venues = (yaml.safe_load((cfg / "venues.yaml").read_text(encoding="utf-8")) or {}).get("venues", [])
    proms = (yaml.safe_load((cfg / "promoters.yaml").read_text(encoding="utf-8")) or {}).get("promoters", [])
    found_v = [v for v in venues if city.venue_named(v["name"], exact=True) or any(city.venue_named(a, exact=True) for a in v.get("aliases") or [])
               or (v.get("socials", {}).get("instagram") and city.venue_by_handle(v["socials"]["instagram"]))]
    known = city.registry["accounts"]
    found_p = [p for p in proms if (p.get("instagram") or "").lower() in known]
    miss_v = [v["name"] for v in venues if v not in found_v and (v.get("km") or 99) <= city.c["radius_km"]]
    miss_p = [p["name"] for p in proms if p not in found_p]
    ev_path = old / "data" / "events.json"
    lines = [f"Locali della lista a mano trovati da soli: {len(found_v)} su {len(venues)}",
             f"Promoter della lista a mano trovati da soli: {len(found_p)} su {len(proms)}",
             "Locali in zona non ancora trovati: " + (", ".join(miss_v) or "nessuno"),
             "Promoter non ancora trovati: " + (", ".join(miss_p[:40]) or "nessuno")]
    if ev_path.exists():
        old_ev = json.loads(ev_path.read_text(encoding="utf-8"))
        new = json.loads((city.data / "events.json").read_text(encoding="utf-8")) if (city.data / "events.json").exists() else {}
        keys_new = {(e["date"], norm(e["venue"])) for e in new.values()}
        def night(e):
            s = e["start"][:16]
            from datetime import date, datetime, timedelta
            dt = datetime.fromisoformat(s)
            return (dt.date() - timedelta(days=1) if dt.hour < 6 else dt.date()).isoformat()
        club_old = [e for e in old_ev if e.get("category") == "dj_club" or e.get("scope") in ("LOCAL", "local")]
        both = [e for e in club_old if (night(e), norm(e["venue"]["name"])) in keys_new]
        lines.append(f"Serate della versione 1 trovate anche dalla nuova: {len(both)} su {len(club_old)}")
    return "\n".join(lines)
