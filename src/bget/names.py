"""Display names instead of Instagram handles (Filippo, 2026-11-17: the posts show profile names).

Order: the name in venues.yaml / promoters.yaml for a known handle, then the profile name read from
Instagram (cached in data/ig_review.json -> profile_names), else the handle as written.
"""
from __future__ import annotations

import json
import re

# a handle written without the @: has a dot or underscore, or is one lowercase word with digits
HANDLE = re.compile(r"(?<![\w@.])@?([a-z0-9_]*[a-z][a-z0-9_]*(?:[._][a-z0-9_]+)+|[a-z0-9_]*_[a-z0-9_]*)(?![\w])")


def handles_in(text: str) -> list[str]:
    """Handles on line-up lines of a caption ("in console marco.foresti_ francescosciretta")."""
    from .headliners import lineup_names
    out = []
    for name in lineup_names(text or ""):
        for tok in name.split():
            if HANDLE.fullmatch(tok.lstrip("@")) and tok.lstrip("@") not in out:
                out.append(tok.lstrip("@"))
    return out


def known_names(cfg) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in cfg.venues:
        if (h := (v.get("socials") or {}).get("instagram")):
            out[h.lower()] = v["name"]
    for p in cfg.promoters or []:
        if p.get("instagram"):
            out[p["instagram"].lower()] = p.get("party_name") or p["name"]
    return out


def name_map(cfg) -> dict[str, str]:
    """handle (lowercase, no @) -> display name."""
    out: dict[str, str] = {}
    path = cfg.root / "data" / "ig_review.json"
    if path.exists():
        try:
            out.update({h: n for h, n in json.loads(path.read_text()).get("profile_names", {}).items() if n})
        except ValueError:
            pass
    out.update(known_names(cfg))
    return out


def display(name: str, names: dict[str, str]) -> str:
    return names.get(name.lstrip("@").lower(), name)
