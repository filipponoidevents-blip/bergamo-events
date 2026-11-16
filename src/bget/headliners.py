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
        own = {_key(h) for h in [ev.venue.socials.instagram, ev.promoter.instagram if ev.promoter else None] if h}
        for a in ev.artists:
            k = _key(a.socials.instagram or a.name)
            if k in own:
                continue
            name = self.by_key.get(k) or a.name
            if name not in found:
                found.append(name)
        return found[:n]
