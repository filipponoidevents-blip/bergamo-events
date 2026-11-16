"""Phase 2 baseline: weekly / monthly carousel slides (1080x1350, Instagram 4:5 portrait).

Deliberately plain: a dark layout with one accent colour. Branding (logo, fonts, palette)
is meant to be swapped in via THEME once the account's look is decided.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from .models import Event

W, H = 1080, 1350
THEME = {
    "bg": (12, 12, 16),
    "fg": (245, 245, 245),
    "muted": (150, 150, 165),
    "accent": (255, 64, 129),
    "font_bold": "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
    "font_reg": "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
    "handle": "@seratebergamo",
}
GIORNI = ["LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM"]
MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio",
        "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
PER_SLIDE = 5


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(THEME["font_bold" if bold else "font_reg"], size)
    except OSError:
        return ImageFont.load_default(size)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), THEME["bg"])
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=THEME["accent"])
    d.text((70, H - 90), THEME["handle"], font=_font(False, 30), fill=THEME["muted"])
    return img, d


def _cover(kicker: str, title: str, subtitle: str, count: int) -> Image.Image:
    img, d = _canvas()
    d.text((70, 380), kicker.upper(), font=_font(True, 40), fill=THEME["accent"])
    y = 450
    for line in title.split("\n"):
        d.text((70, y), line, font=_font(True, 120), fill=THEME["fg"])
        y += 135
    d.text((70, y + 30), subtitle, font=_font(False, 44), fill=THEME["muted"])
    d.text((70, y + 110), f"{count} eventi  ·  swipe →", font=_font(False, 36), fill=THEME["fg"])
    return img


def _list_slide(header: str, rows: list[tuple[str, Event]]) -> Image.Image:
    img, d = _canvas()
    d.text((70, 90), header, font=_font(True, 64), fill=THEME["fg"])
    y = 220
    for label, ev in rows:
        d.text((70, y), label, font=_font(True, 30), fill=THEME["accent"])
        d.text((70, y + 40), _fit(d, ev.title, _font(True, 46), W - 140), font=_font(True, 46), fill=THEME["fg"])
        lineup = ", ".join(a.name for a in ev.artists[:4])
        meta = f"{ev.venue.name} · {ev.venue.city}"
        d.text((70, y + 100), _fit(d, meta, _font(False, 32), W - 140), font=_font(False, 32), fill=THEME["muted"])
        if lineup:
            d.text((70, y + 142), _fit(d, lineup, _font(False, 32), W - 140), font=_font(False, 32), fill=THEME["fg"])
        y += 200
    return img


def _label(ev: Event) -> str:
    n = ev.night
    return f"{GIORNI[n.weekday()]} {n.day} {MESI[n.month][:3].upper()}  ·  {ev.start.strftime('%H:%M')}"


def _chunks(rows: list, n: int) -> list[list]:
    return [rows[i:i + n] for i in range(0, len(rows), n)] or [[]]


Slide = tuple[Image.Image, list[Event]]   # image + the events shown on it (for tagging)


def weekly(events: Iterable[Event], week_start: date, alerts: Iterable[Event] = ()) -> list[Slide]:
    week_end = week_start + timedelta(days=6)
    evs = sorted((e for e in events if week_start <= e.night <= week_end), key=lambda e: e.start)
    al = sorted((e for e in alerts if week_start <= e.night <= week_end), key=lambda e: e.start)
    span = f"{week_start.day} {MESI[week_start.month]} – {week_end.day} {MESI[week_end.month]}"
    slides = [(_cover("Questa settimana", "Bergamo\nNights", span, len(evs)), [])]
    for chunk in _chunks([(_label(e), e) for e in evs], PER_SLIDE):
        if chunk:
            slides.append((_list_slide("In zona", chunk), [e for _, e in chunk]))
    for chunk in _chunks([(_label(e) + f"  ·  {e.venue.province or ''}", e) for e in al], PER_SLIDE):
        if chunk:
            slides.append((_list_slide("Vale il viaggio", chunk), [e for _, e in chunk]))
    return slides


def monthly(events: Iterable[Event], year: int, month: int, alerts: Iterable[Event] = ()) -> list[Slide]:
    evs = sorted((e for e in events if (e.night.year, e.night.month) == (year, month)), key=lambda e: e.start)
    al = sorted((e for e in alerts if (e.night.year, e.night.month) == (year, month)), key=lambda e: e.start)
    slides = [(_cover("Il mese", f"{MESI[month]}\n{year}", "Bergamo e dintorni", len(evs)), [])]
    by_week: dict[int, list[Event]] = defaultdict(list)
    for e in evs:
        by_week[e.night.isocalendar()[1]].append(e)
    for wk in sorted(by_week):
        items = by_week[wk]
        first = min(e.night for e in items)
        monday = first - timedelta(days=first.weekday())
        header = f"Settimana {monday.day}–{(monday + timedelta(days=6)).day}"
        for chunk in _chunks([(_label(e), e) for e in items], PER_SLIDE):
            slides.append((_list_slide(header, chunk), [e for _, e in chunk]))
    for chunk in _chunks([(_label(e) + f"  ·  {e.venue.province or ''}", e) for e in al], PER_SLIDE):
        if chunk:
            slides.append((_list_slide("Vale il viaggio", chunk), [e for _, e in chunk]))
    return slides


def save(slides: list[Slide], outdir: Path, stem: str) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, (s, _) in enumerate(slides, 1):
        p = outdir / f"{stem}_{i:02d}.png"
        s.save(p, optimize=True)
        paths.append(p)
    return paths
