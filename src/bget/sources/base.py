from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable

import requests

from ..config import Config
from ..models import Event

USER_AGENT = "bergamo-events-tracker/0.1 (personal event calendar)"


class Source(ABC):
    """A collector. Turns one configured source into Event objects (unscored, unenriched)."""

    type: str = "base"

    def __init__(self, spec: dict[str, Any], cfg: Config):
        self.spec = spec
        self.cfg = cfg
        self.id = spec["id"]

    @abstractmethod
    def collect(self) -> Iterable[Event]: ...

    # helpers ---------------------------------------------------------------
    def fetch(self, url: str) -> str:
        if url.startswith("file://"):
            p = Path(url[7:])
            return (p if p.is_absolute() else self.cfg.root / p).read_text()
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        return r.text


class NotImplementedSource(Source):
    """Placeholder for adapters designed but not built yet."""

    note = ""

    def collect(self) -> Iterable[Event]:
        raise NotImplementedError(f"{self.type} adapter is not built yet. {self.note}")
