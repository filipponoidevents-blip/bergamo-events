"""Find the notable artists / headliners of a night.

Names come from config/watchlist.yaml (dj_headliners, crossover_live and notable_artists, which
Filippo edits). A name counts when it is written in the title, caption or flyer text, or when one of
the post's @mentions is that artist (e.g. @ilarioalicante). Line-up @mentions that are not on the
list follow, so a night always shows who plays when the post says so.
"""
from __future__ import annotations

import re

from .config import Config
from .models import Event, slug


# "Dj: StefanOne + Andrew V.", "Line up: A b2b B", "Special guest - C", "Main room: D"
LINEUP_LINE = re.compile(r"^\W*(?:dj'?s?(?:\s*set)?|line\s*-?\s*up|in\s+consolle|consolle|console|special\s+guests?|"
                         r"guests?|headliners?|main\s*room|club\s*room|room\s*\d|resident|feat\.?|ft\.?)[ \t]*[:\-–—][ \t]*(\S.*)$",
                         re.I | re.M)
SPLIT = re.compile(r"\s*(?:\+|,|&|\bb2b\b|\bx\b|/|\be\b|\band\b|·|\|)\s*", re.I)


def lineup_names(text: str) -> list[str]:
    """Names written on line-up lines of a caption or flyer."""
    out: list[str] = []
    for m in LINEUP_LINE.finditer(text or ""):
        for part in SPLIT.split(m.group(1)):
            part = re.sub(r"[@#]", "", part).strip(" .!-–—:\"'“”")
            if 2 <= len(part) <= 30 and not re.search(r"\d{1,2}[:.]\d{2}", part) and part not in out:
                out.append(part)
    return out


def _key(s: str) -> str:
    return slug(s).replace("-", "")


class HeadlinerFinder:
    def __init__(self, cfg: Config):
        wl = cfg.watchlist
        names = [*wl.get("dj_headliners", []), *wl.get("notable_artists", []), *wl.get("crossover_live", [])]
        self.names = list(dict.fromkeys(str(n) for n in names if n))
        self.rx = [(n, re.compile(rf"(?<![\w]){re.escape(n)}(?![\w])", re.I)) for n in self.names]
        self.by_key = {_key(n): n for n in self.names}

    def find(self, ev: Event, n: int = 3) -> list[str]:
        text = f"{ev.title}\n{ev.description}"
        found = [name for name, rx in self.rx if rx.search(text)]
        for name in lineup_names(ev.description):
            name = self.by_key.get(_key(name), name)
            if name not in found:
                found.append(name)
        own = {_key(h) for h in [ev.venue.socials.instagram, ev.promoter.instagram if ev.promoter else None] if h}
        for a in ev.artists:
            k = _key(a.socials.instagram or a.name)
            if k in own:
                continue
            name = self.by_key.get(k) or a.name
            if name not in found:
                found.append(name)
        return found[:n]
