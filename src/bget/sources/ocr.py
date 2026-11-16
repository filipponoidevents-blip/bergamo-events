"""Free text recognition for flyer images (Tesseract). Install: apt-get install tesseract-ocr tesseract-ocr-ita."""
from __future__ import annotations

import io
import logging
import shutil
import subprocess

import requests
from PIL import Image, ImageOps

log = logging.getLogger(__name__)


def available() -> bool:
    return shutil.which("tesseract") is not None


def _tesseract(img: Image.Image, langs: str) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = subprocess.run(["tesseract", "stdin", "stdout", "-l", langs, "--psm", "4"],
                       input=buf.getvalue(), capture_output=True, timeout=120)
    return r.stdout.decode("utf-8", "ignore")


def _score(text: str) -> int:
    return sum(c.isalpha() for c in text)


def ocr_bytes(data: bytes, langs: str = "ita+eng") -> str:
    """Flyers are often light text on dark backgrounds, so try both polarities and keep the better read."""
    img = Image.open(io.BytesIO(data)).convert("L")
    if img.width < 1600:
        f = 1600 / img.width
        img = img.resize((1600, int(img.height * f)), Image.LANCZOS)
    img = ImageOps.autocontrast(img)
    reads = [_tesseract(img, langs), _tesseract(ImageOps.invert(img), langs)]
    return max(reads, key=_score)


def ocr_url(url: str, langs: str = "ita+eng") -> str:
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return ocr_bytes(r.content, langs)
    except Exception as e:  # noqa: BLE001
        log.warning("ocr failed for %s: %s", url[:80], e)
        return ""
