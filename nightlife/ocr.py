"""Testo scritto sulle locandine (OCR con Tesseract, gratis). Sul flyer spesso ci sono promoter, ospiti e @ che la didascalia non dice.

Su GitHub Tesseract si installa nel workflow; se manca, la lettura delle immagini viene saltata senza errori.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess

import requests

from .util import UA

log = logging.getLogger("nightlife.ocr")
AVAILABLE = shutil.which("tesseract") is not None


def read_image(url: str, timeout: int = 25) -> str | None:
    """Testo dell'immagine, una riga per riga leggibile. None se non si può leggere (Tesseract assente, link scaduto...)."""
    if not AVAILABLE or not url:
        return None
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        r.raise_for_status()
        out = subprocess.run(["tesseract", "stdin", "stdout", "-l", "ita+eng", "--psm", "3"], input=r.content,
                             capture_output=True, timeout=60).stdout.decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        log.info("ocr %s: %s", url[:60], e)
        return None
    lines = []
    for line in out.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" |_-—~*'\".,;:")
        if len(re.findall(r"[A-Za-z0-9]", line)) >= 3:   # via i pezzi di grafica letti come segni
            lines.append(line)
    return "\n".join(lines)
