"""Piccoli attrezzi comuni: testo, distanze, date, richieste web."""
from __future__ import annotations

import math
import re
import time
import unicodedata
from datetime import date, datetime, timedelta

import requests
import socket
import urllib3.util.connection as _uc

# Alcuni server (OpenStreetMap) rispondono solo in IPv4 dalla rete di GitHub: si usa sempre IPv4.
_uc.allowed_gai_family = lambda: socket.AF_INET

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"}


def plain(text: str) -> str:
    """Testo normale: i caratteri "decorati" di Instagram (𝐏𝐎𝐏) tornano lettere, niente accenti."""
    t = unicodedata.normalize("NFKC", text or "")
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c))


def norm(text: str) -> str:
    """Chiave di confronto: minuscole, solo lettere e numeri separati da spazi."""
    return re.sub(r"[^a-z0-9]+", " ", plain(text).lower()).strip()


def contains(haystack_norm: str, needle: str) -> bool:
    n = norm(needle)
    return bool(n) and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", haystack_norm) is not None


def km_between(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(a)), 1)


def scale(value, floor, top, log=True) -> float:
    """Porta un numero (follower, capienza) su una scala 0-10."""
    if not value or value <= floor:
        return 0.0
    if value >= top:
        return 10.0
    if log:
        return round(10 * math.log(value / floor) / math.log(top / floor), 1)
    return round(10 * (value - floor) / (top - floor), 1)


def hhmm(text) -> int | None:
    """'23:30' -> minuti da mezzanotte."""
    if not text:
        return None
    h, m = str(text).split(":")
    return int(h) * 60 + int(m)


def easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    j, k = c // 4, c % 4
    m = (a + 11 * h) // 319
    r = (2 * e + 2 * j - k - h + m + 32) % 7
    month = (h - m + r + 90) // 25
    day = (h - m + r + month + 19) % 32
    return date(year, month, day)


def holidays(year: int) -> dict[date, str]:
    """Festività italiane: per i promoter una vigilia vale come un sabato."""
    fixed = {(1, 1): "Capodanno", (1, 6): "Epifania", (4, 25): "Liberazione", (5, 1): "Primo maggio", (6, 2): "Festa della Repubblica",
             (8, 15): "Ferragosto", (11, 1): "Ognissanti", (12, 8): "Immacolata", (12, 25): "Natale", (12, 26): "Santo Stefano"}
    out = {date(year, m, d): n for (m, d), n in fixed.items()}
    out[easter(year)] = "Pasqua"
    out[easter(year) + timedelta(days=1)] = "Pasquetta"
    return out


def day_note(d: date) -> str | None:
    """'vigilia di Natale', 'Halloween'... quello che rende speciale una sera."""
    special = {(10, 31): "Halloween", (12, 31): "Capodanno (veglione)", (12, 24): "Vigilia di Natale", (2, 14): "San Valentino", (8, 14): "Vigilia di Ferragosto"}
    if (d.month, d.day) in special:
        return special[(d.month, d.day)]
    hol = {**holidays(d.year), **holidays(d.year + 1)}
    if d + timedelta(days=1) in hol:
        return f"prefestivo ({hol[d + timedelta(days=1)]})"
    if d in hol:
        return f"festivo ({hol[d]})"
    return None


_last_call: dict[str, float] = {}


def fetch(url: str, *, method="GET", pause=1.0, timeout=30, **kw) -> requests.Response:
    """Richiesta web educata: una pausa tra due chiamate allo stesso sito, errore chiaro se va male."""
    host = re.sub(r"^https?://([^/]+).*", r"\1", url)
    wait = pause - (time.time() - _last_call.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    headers = {**UA, **kw.pop("headers", {})}
    if kw.pop("bot_ua", False):   # i servizi di OpenStreetMap chiedono un nome di programma, non quello di un browser
        headers["User-Agent"] = "nightlife-tracker/2.0 (open-source event calendar)"
    r = requests.request(method, url, headers=headers, timeout=timeout, **kw)
    _last_call[host] = time.time()
    r.raise_for_status()
    return r


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
