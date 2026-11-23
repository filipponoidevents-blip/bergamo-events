"""Una città: impostazioni, anagrafica (locali, profili Instagram) e conoscenze facoltative.

- cities/<nome>/city.yaml        impostazioni scritte una volta (centro, raggio, fonti)
- cities/<nome>/data/registry.json   anagrafica scritta dal programma (scoperta automatica)
- cities/<nome>/conoscenze.yaml  facoltativo: correzioni e cose che solo una persona sa (voti, generi, sale)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from .util import contains, km_between, norm, plain

ROOT = Path(__file__).resolve().parent
EMOJI = re.compile(r"[^\w\s.,&'’+\-/()|!:·]", re.U)


def clean_name(name: str) -> str:
    """Nome di profilo senza emoji e decorazioni: 'TABARIN🍸' -> 'TABARIN'."""
    t = EMOJI.sub(" ", plain(name or ""))
    t = re.sub(r"\s+", " ", t).strip(" -|·.,")
    return t


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def read_yaml(path: Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


GENERIC = {"lounge", "bar", "ristorante", "restaurant", "risto", "cocktail", "cocktails", "club", "discoteca", "disco", "pub", "food", "drink", "drinks", "and", "more",
           "caffe", "cafe", "caffetteria", "pizzeria", "music", "musik", "wine", "beer", "the", "il", "la", "lo", "le", "di", "del", "della", "e", "bistrot", "bergamo", "night"}
COMMON = {"beach", "summer", "garden", "moonlight", "tramonto", "bloom", "charlie", "level", "fashion", "barrier", "ombra", "zero", "drop", "limbo",
          "origami", "tiny", "karma", "sprint", "vibe", "hype", "queen", "feeling", "notturna", "terminal",
          "milano", "bergamo", "brescia", "roma", "torino", "monza", "lecco", "como", "cremona", "verona", "venezia", "napoli", "bologna", "firenze", "genova", "italia",
          "musica", "musicale", "live", "eventi", "events", "sound", "festa", "party", "notte", "arte", "spazio", "teatro", "cinema", "centro"}


def core_name(name: str) -> str | None:
    """Il nome del locale senza le parole generiche, se resta qualcosa di riconoscibile da solo."""
    words = [w for w in norm(re.split(r"[|/(]", name or "")[0]).split() if w not in GENERIC]
    core = " ".join(words)
    if len(core) < 4 or (len(words) == 1 and core in COMMON):   # una parola qualunque ("zero", "vibe") farebbe confusione nei testi
        return None
    return core


def merge_knowledge(base: dict, mine: dict) -> dict:
    """Conoscenze importate + conoscenze scritte a mano: le liste si sommano e le voci a mano vengono dopo, quindi vincono."""
    out = dict(base)
    for k, v in (mine or {}).items():
        out[k] = (out.get(k) or []) + v if isinstance(v, list) and isinstance(out.get(k), list) else v
    return out


class City:
    def __init__(self, folder: str | Path, knowledge: bool = True):
        self.dir = Path(folder)
        self.slug = self.dir.name
        self.settings = deep_merge(read_yaml(ROOT / "defaults" / "settings.yaml"), read_yaml(self.dir / "city.yaml"))
        self.c = self.settings["city"]
        self.know = merge_knowledge(read_yaml(self.dir / "conoscenze-importate.yaml"), read_yaml(self.dir / "conoscenze.yaml")) if knowledge else {}
        self.data = self.dir / "data"
        self.out = self.dir / "out"
        self.data.mkdir(parents=True, exist_ok=True)
        reg = self.data / "registry.json"
        self.registry = json.loads(reg.read_text(encoding="utf-8")) if reg.exists() else {}
        self.registry.setdefault("venues", {})
        self.registry.setdefault("accounts", {})
        self.artists = {norm(a["name"]): a for a in read_yaml(ROOT / "defaults" / "artists.yaml").get("artists", []) + (self.know.get("artists") or [])}
        self.brands = read_yaml(ROOT / "defaults" / "brands.yaml").get("brands", []) + (self.know.get("brands") or [])
        self._build()

    # ---- anagrafica + conoscenze -------------------------------------------------
    def _build(self) -> None:
        """Unisce l'anagrafica automatica con le conoscenze: quello che scrive una persona vince sempre."""
        self.venues: list[dict] = [dict(v) for v in self.registry["venues"].values() if not v.get("removed")]
        self._index = None
        for patch in self.know.get("venues") or []:
            v = self.venue_named(patch.get("name"), exact=True) or (self.venue_by_handle(patch.get("instagram")) if patch.get("instagram") else None)
            if v is None:
                v = {"name": patch["name"], "sources": ["conoscenze"]}
                self.venues.append(v)
            v.update({k: x for k, x in patch.items() if x is not None})
            if patch.get("remove"):   # "non è nightlife": il locale sparisce da tutto
                self.venues.remove(v)
            else:
                self._absorb_twins(v)
            self._index = None
        self.promoters: dict[str, dict] = {}
        for h, a in self.registry["accounts"].items():
            if a.get("kind") == "promoter":
                self.promoters[h] = {"instagram": h, "name": clean_name(a.get("name") or h), "followers": a.get("followers"),
                                     **({"party_name": a["party_name"]} if a.get("party_name") else {})}
        for patch in self.know.get("promoters") or []:
            h = (patch.get("instagram") or norm(patch["name"]).replace(" ", "")).lower()
            self.promoters[h] = {**self.promoters.get(h, {"instagram": patch.get("instagram")}), **patch}
        for v in self.venues:
            if v.get("lat") is not None and v.get("km") is None:
                v["km"] = km_between(self.c["lat"], self.c["lon"], v["lat"], v["lon"])
        self._index = None

    def _absorb_twins(self, v: dict) -> None:
        """Lo stesso locale trovato due volte dal sistema (es. "NXT Station" e "NXT Bergamo", a 50 m): resta quello delle conoscenze."""
        if v.get("lat") is None:
            return
        words = {w for n in [v["name"], *(v.get("aliases") or [])] for w in norm(n).split() if len(w) >= 3 and w not in GENERIC}
        for o in list(self.venues):
            if o is v or o.get("lat") is None or km_between(v["lat"], v["lon"], o["lat"], o["lon"]) > 0.3:
                continue
            same = (o.get("instagram") and o.get("instagram") == v.get("instagram")) or words & {w for w in norm(o["name"]).split() if w not in GENERIC}
            if same and not any(norm(o["name"]) == norm(p.get("name") or "") for p in self.know.get("venues") or []):
                self.venues.remove(o)
                v["aliases"] = sorted({*(v.get("aliases") or []), o["name"]})

    def _aliases(self):
        if self._index is None:
            pairs = []
            for v in self.venues:
                for a in [v["name"], *(v.get("aliases") or [])]:
                    if len(norm(a)) >= 3:
                        pairs.append((norm(a), v))
                core = core_name(v["name"])   # "Mint Lounge Bar" -> "mint", "Ristorante BV Rooftop lounge bar" -> "bv rooftop"
                if core and core != norm(v["name"]):
                    pairs.append((core, v))
            self._index = sorted(pairs, key=lambda p: -len(p[0]))
        return self._index

    def venue_named(self, name: str | None, exact=False) -> dict | None:
        n = norm(name or "")
        if not n:
            return None
        for a, v in self._aliases():
            if a == n:
                return v
        if exact:
            return None
        for a, v in self._aliases():
            if len(a) >= 4 and (contains(n, a) or contains(a, n)):
                return v
        return None

    def venue_in_text(self, text_norm: str) -> dict | None:
        """Il locale nominato in un testo (vince il nome più lungo)."""
        for a, v in self._aliases():
            if len(a) >= 4 and contains(text_norm, a):
                return v
        return None

    def venue_by_handle(self, handle: str | None) -> dict | None:
        h = (handle or "").lower().lstrip("@")
        return next((v for v in self.venues if (v.get("instagram") or "").lower() == h), None) if h else None

    def account(self, handle: str) -> dict:
        return self.registry["accounts"].get((handle or "").lower().lstrip("@"), {})

    def followers(self, handle: str | None) -> int | None:
        return self.account(handle).get("followers") if handle else None

    def promoter(self, handle: str | None) -> dict | None:
        return self.promoters.get((handle or "").lower().lstrip("@")) if handle else None

    def promoter_in_text(self, text_norm: str) -> dict | None:
        best = None
        for p in self.promoters.values():
            for a in [p.get("party_name"), p["name"], *(p.get("aliases") or [])]:
                if a and len(norm(a)) >= 4 and contains(text_norm, a) and (best is None or len(norm(a)) > best[0]):
                    best = (len(norm(a)), p)
        return best[1] if best else None

    def artist_level(self, name: str) -> str | None:
        a = self.artists.get(norm(name))
        return a.get("level") if a else None

    def known_artists_in(self, text_norm: str) -> list[dict]:
        return [a for k, a in self.artists.items() if len(k) >= 4 and contains(text_norm, k)]

    def brand_in(self, text_norm: str) -> str | None:
        return next((b for b in self.brands if contains(text_norm, b)), None)

    def distance(self, lat, lon) -> float | None:
        return km_between(self.c["lat"], self.c["lon"], lat, lon) if lat is not None and lon is not None else None

    def watch_list(self) -> list[str]:
        """I profili Instagram da leggere: locali, promoter, semi iniziali. I più importanti per primi."""
        seen, out = set(), []
        def add(h, weight):
            h = (h or "").lower().lstrip("@")
            a = self.account(h)
            if h and h not in seen and a.get("readable") is not False and a.get("kind") not in ("artist", "ignore", "national", "media"):
                seen.add(h)
                out.append((weight, h))
        for v in self.venues:
            if v.get("watch") is not False:
                add(v.get("instagram"), (v.get("vote") or 0) * 1000 + (self.followers(v.get("instagram")) or 0) / 100 + 500)
        for h, p in self.promoters.items():
            if p.get("watch") is not False and p.get("instagram"):
                add(p["instagram"], (p.get("followers") or self.followers(h) or 0) / 100)
        for h in self.know.get("seeds") or []:
            add(h, 100)
        for h, a in self.registry["accounts"].items():
            if a.get("watch"):
                add(h, (a.get("followers") or 0) / 100)
        return [h for _, h in sorted(out, key=lambda x: -x[0])]

    def save(self) -> None:
        (self.data / "registry.json").write_text(json.dumps(self.registry, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        self._build()
