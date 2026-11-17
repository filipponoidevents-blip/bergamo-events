"""Instagram / TikTok slides in the "wall calendar" style Filippo chose (2026-11-17).

Three posts, each rendered as Instagram 4:5 (1080x1350) and TikTok 9:16 (1080x1920):
- monthly:  month grid with a dot on every date with a club night, then the date slides
- weekly:   "16—22 NOV" + a 7-day strip with dots and the number of nights, then the date slides
- midweek:  "Prima della serata": pre-party (aperitivo) and non-music entertainment, Thu-Sun

Club posts are white on red; the midweek post is red on white (the strong red is for the real
nights). Date slides: the date on top, that day's events below, one date per slide (packed
several per slide only when a month would go past Instagram's 10-image carousel limit).
Small info runs along the top and bottom edges. Fonts are bundled (SIL OFL, see fonts/).
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from .calendar_ics import ROME, headliners, party_name
from .models import Event

FONTS = Path(__file__).parent / "fonts"
HANDLE = "@seratebergamo"
AREA = "BERGAMO E DINTORNI"
RED, WHITE = (236, 40, 56), (255, 255, 255)
CLUB = {"bg": RED, "fg": WHITE}
OTHER = {"bg": WHITE, "fg": RED}
# width, height, (top, bottom) of the area kept clear of the app's own buttons and caption
FORMATS = {"ig": (1080, 1350, (30, 1330)), "tt": (1080, 1920, (150, 1560))}
MAX_SLIDES = 10   # Instagram API carousel limit
MX = 60           # side margin

GIORNI = ["LUN", "MAR", "MER", "GIO", "VEN", "SAB", "DOM"]
GIORNI_LUNGHI = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
MESI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio",
        "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
# non-club categories that go in the midweek post (sources/categories.py), with their label
MIDWEEK_KINDS = {"food_drink": "PRESERATA", "comedy": "COMEDY", "quiz_games": "QUIZ", "culture": "CULTURA"}

Slide = tuple[Image.Image, list[Event]]
   # image + the events shown on it (for tagging)


def _font(name: str, size: int, weight: int | None = None, width: int | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight or width:
        axes = []
        for a in f.get_variation_axes():
            n = a["name"].decode() if isinstance(a["name"], bytes) else a["name"]
            axes.append({"Weight": weight, "Width": width}.get(n) or a["default"])
        f.set_variation_by_axes(axes)
    return f


def chunk(size: int) -> ImageFont.FreeTypeFont:      # display: month, dates, venues
    return _font("Unbounded.ttf", size, weight=900)


def grot(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:   # text
    return _font("Archivo.ttf", size, weight=weight, width=100)


def mono(size: int) -> ImageFont.FreeTypeFont:       # edge info
    return _font("SpaceMono-Bold.ttf", size)


def _fit(d: ImageDraw.ImageDraw, text: str, make, size: int, max_w: float, min_size: int = 26):
    """Shrink until it fits; below min_size, cut the text with an ellipsis."""
    while size > min_size and d.textlength(text, font=make(size)) > max_w:
        size -= 2
    f = make(size)
    if d.textlength(text, font=f) > max_w:
        while text and d.textlength(text + "…", font=f) > max_w:
            text = text[:-1]
        text = text.rstrip() + "…"
    return text, f


@dataclass
class Page:
    img: Image.Image
    d: ImageDraw.ImageDraw
    w: int
    h: int
    top: int
    bot: int
    bg: tuple
    fg: tuple


def _page(fmt: str, pal: dict, top_mid: str, bottom_mid: str, bottom_right: str) -> Page:
    w, h, (top, bot) = FORMATS[fmt]
    img = Image.new("RGB", (w, h), pal["bg"])
    p = Page(img, ImageDraw.Draw(img), w, h, top, bot, pal["bg"], pal["fg"])
    _edges(p, ["SERATE BERGAMO", top_mid, HANDLE], [AREA, bottom_mid, bottom_right])
    return p


def _edges(p: Page, top_items: list[str], bottom_items: list[str]) -> None:
    """The small info along the top and bottom edges."""
    m = mono(20)
    for items, y in ((top_items, p.top), (bottom_items, p.bot - 24)):
        for i, t in enumerate(items):
            x = MX + (p.w - 2 * MX) * i / (len(items) - 1)
            p.d.text((x, y), t, font=m, fill=p.fg, anchor="la" if i == 0 else ("ra" if i == len(items) - 1 else "ma"))


def _dot(p: Page, cx: float, cy: float, r: float, label: str, size: int) -> None:
    p.d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=p.fg)
    p.d.text((cx, cy), label, font=grot(size, 800), fill=p.bg, anchor="mm")


def _big_title(p: Page, lines: list[str], y: int, align: str = "right") -> int:
    for t in lines:
        f = chunk(400)
        while p.d.textlength(t, font=f) > p.w - 2 * MX - 10:
            f = chunk(f.size - 6)
        if align == "right":
            p.d.text((p.w - MX, y), t, font=f, fill=p.fg, anchor="ra")
        else:
            p.d.text((MX - 6, y), t, font=f, fill=p.fg)
        y += int(f.size * 0.98)
    return y


# ---------- covers ----------

def _month_cover(fmt: str, year: int, month: int, nights: dict[date, int]) -> Image.Image:
    p = _page(fmt, CLUB, f"N°{month:02d}", "SOLO DJ & CLUB", f"{MESI[month][:3].upper()} {year}")
    d, R = p.d, p.w - MX
    y = _big_title(p, [MESI[month][:3].upper()], p.top + 50) + 30
    cw = (R - MX) / 7
    for i, g in enumerate(GIORNI):
        d.text((MX + cw * i + cw / 2, y), g, font=chunk(22), fill=p.fg, anchor="ma")
    y += 50
    weeks = calendar.Calendar(0).monthdayscalendar(year, month)
    gb = p.bot - 110
    ch = (gb - y) / len(weeks)
    d.rectangle([MX, y, R, y + ch * len(weeks)], outline=p.fg, width=2)
    for i in range(1, 7):
        d.line([(MX + cw * i, y), (MX + cw * i, y + ch * len(weeks))], fill=p.fg, width=2)
    for j in range(1, len(weeks)):
        d.line([(MX, y + ch * j), (R, y + ch * j)], fill=p.fg, width=2)
    for j, wk in enumerate(weeks):
        for i, day in enumerate(wk):
            if not day:
                continue
            cx, cy = MX + cw * i + 34, y + ch * j + 32
            if date(year, month, day) in nights:
                _dot(p, cx, cy, min(cw, ch) * 0.3, str(day), 30)
            else:
                d.text((cx, cy), str(day), font=grot(30, 500), fill=p.fg, anchor="mm")
    y = y + ch * len(weeks) + 24
    total = sum(nights.values())
    lab = chunk(30)
    d.text((MX, y), "SERATE:", font=lab, fill=p.fg)
    d.text((MX + d.textlength("SERATE: ", font=lab), y + 4), str(total), font=grot(30, 500), fill=p.fg)
    return p.img


def _strip_cover(fmt: str, pal: dict, title: list[str], subtitle: str | None, days: list[date],
                 counts: dict[date, int], labels: dict[date, list[str]] | None, top_mid: str,
                 bottom_mid: str) -> Image.Image:
    """Weekly and midweek cover: big title, then one column per day with a dot on busy days."""
    last = days[-1]
    p = _page(fmt, pal, top_mid, bottom_mid, f"{MESI[last.month][:3].upper()} {last.year}")
    d, R = p.d, p.w - MX
    y = _big_title(p, title, p.top + 50, align="right" if pal is CLUB else "left") + 20
    if subtitle:
        d.line([(MX, y), (R, y)], fill=p.fg, width=2)
        y += 24
        d.text((MX, y), subtitle, font=grot(32, 600), fill=p.fg)
        y += 80
    else:
        y += 10
    cw, gb = (R - MX) / len(days), p.bot - 110
    d.rectangle([MX, y, R, gb], outline=p.fg, width=2)
    for i in range(1, len(days)):
        d.line([(MX + cw * i, y), (MX + cw * i, gb)], fill=p.fg, width=2)
    for i, day in enumerate(days):
        x = MX + cw * i + cw / 2
        d.text((x, y + 28), GIORNI[day.weekday()], font=chunk(22 if len(days) > 4 else 28), fill=p.fg, anchor="ma")
        r, cy = min(cw * 0.36, (gb - y) * 0.2), y + (gb - y) * 0.45
        if counts.get(day):
            _dot(p, x, cy, r, str(day.day), int(r * 0.85))
        else:
            d.text((x, cy), str(day.day), font=grot(40, 500), fill=p.fg, anchor="mm")
        for k, lab in enumerate((labels or {}).get(day, [])):
            d.text((x, cy + r + 30 + k * 34), lab, font=grot(24, 700), fill=p.fg, anchor="ma")
        if counts.get(day) and labels is None:
            n = counts[day]
            d.text((x, gb - 30), str(n), font=chunk(28), fill=p.fg, anchor="ms")
            d.text((x, gb - 12), "serate" if n > 1 else "serata", font=grot(18, 600), fill=p.fg, anchor="ms")
    if labels is None:
        lab = chunk(30)
        d.text((MX, gb + 24), "SERATE:", font=lab, fill=p.fg)
        d.text((MX + d.textlength("SERATE: ", font=lab), gb + 28), str(sum(counts.values())), font=grot(30, 500), fill=p.fg)
    return p.img


# ---------- date slides ----------

def clean(text: str) -> str:
    """Instagram captions use "fancy" letters (𝐁𝐎𝐋𝐃, 𝓈𝒸𝓇𝒾𝓅𝓉) and emoji the fonts don't have:
    fold them to plain letters and drop what is left over."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if ch.isalnum() or ch in " .,:;!?'’&/·+-—()\"@#%" or ch.isspace())
    return re.sub(r"\s+", " ", text).strip(" -·,")


@dataclass
class Row:
    time: str
    tag: str            # genre (club) or category label (midweek)
    head: str           # big line: venue (club) or event name (midweek)
    sub: str = ""       # party name (club) or venue (midweek)
    extra: str = ""     # "w/ headliner"
    label: str = ""     # pill before the tag, e.g. PRESERATA on a club slide


def club_row(e: Event, preserata: bool = False, tag_pill: bool = False) -> Row:
    genre = e.genre if e.genre and e.genre != "Unspecified" else ""
    head = headliners(e)
    return Row(e.start.astimezone(ROME).strftime("%H:%M") if e.start_known else "", genre.upper(),
               clean(e.venue.name).upper(), clean(party_name(e)), f"w/ {clean(head)}" if head else "",
               "PRESERATA" if preserata else "")


def other_row(kind: str, e: Event) -> Row:
    return Row(e.start.astimezone(ROME).strftime("%H:%M"), MIDWEEK_KINDS.get(kind, kind.upper()),
               clean(party_name(e) or e.title).upper(), clean(e.venue.name), "")


DENSE_ROW = 64


def _row_height(r: Row, pill: bool, dense: bool = False) -> int:
    if dense:
        return DENSE_ROW
    return 24 + 64 + 76 + (52 if r.sub else 0) + (52 if r.extra else 0) + 12


def _draw_dense(p: Page, y: int, r: Row) -> int:
    """One line per event, for the monthly post: 23:00  VENUE  party / headliner."""
    d, R = p.d, p.w - MX
    y += 14
    d.text((MX, y), r.time, font=chunk(28), fill=p.fg)
    x = MX + 150
    text, f = _fit(d, r.head, chunk, 32, (R - x) * 0.62, min_size=22)
    d.text((x, y), text, font=f, fill=p.fg)
    x += d.textlength(text, font=f) + 20
    rest = " · ".join(t for t in [r.label.capitalize(), r.tag.title(), r.sub, r.extra] if t)
    if rest and R - x > 80:
        text, f = _fit(d, rest, lambda z: grot(z, 600), 28, R - x, min_size=20)
        d.text((x, y + 4), text, font=f, fill=p.fg)
    y += DENSE_ROW - 14
    d.line([(MX, y), (R, y)], fill=p.fg, width=1)
    return y


def _draw_row(p: Page, y: int, r: Row, pill: bool) -> int:
    d, R = p.d, p.w - MX
    y += 24
    d.text((MX, y), r.time, font=chunk(40), fill=p.fg)
    tx = MX + (d.textlength(r.time, font=chunk(40)) + 30 if r.time else 0)
    if r.label:
        f = grot(24, 800)
        d.rounded_rectangle([tx, y + 2, tx + d.textlength(r.label, font=f) + 36, y + 46], 22, fill=p.fg)
        d.text((tx + 18, y + 24), r.label, font=f, fill=p.bg, anchor="lm")
        tx += d.textlength(r.label, font=f) + 56
    if r.tag:
        if pill:
            f = grot(24, 800)
            d.rounded_rectangle([tx, y + 2, tx + d.textlength(r.tag, font=f) + 36, y + 46], 22, fill=p.fg)
            d.text((tx + 18, y + 24), r.tag, font=f, fill=p.bg, anchor="lm")
        else:
            d.text((tx, y + 12), r.tag, font=grot(26, 600), fill=p.fg)
    y += 64
    text, f = _fit(d, r.head, chunk, 62, R - MX)
    d.text((MX, y), text, font=f, fill=p.fg)
    y += 76
    if r.sub:
        text, f = _fit(d, r.sub, lambda s: grot(s, 800), 40, R - MX)
        d.text((MX, y), text, font=f, fill=p.fg)
        y += 52
    if r.extra:
        text, f = _fit(d, r.extra, lambda s: grot(s, 500), 36, R - MX)
        d.text((MX, y), text, font=f, fill=p.fg)
        y += 52
    y += 12
    d.line([(MX, y), (R, y)], fill=p.fg, width=2)
    return y


def _date_header(p: Page, y: int, day: date | None, label: str | None, compact: bool, small: bool = False) -> int:
    """Big date number with weekday and month beside it (or a word, e.g. FUORI ZONA)."""
    d, R = p.d, p.w - MX
    if day is None:
        text, f = _fit(d, label or "", chunk, 110 if small else 150, R - MX)
        d.text((MX - 4, y), text, font=f, fill=p.fg)
        y += int(f.size * 1.2)
    else:
        big = chunk(110 if small else 150 if compact else 300)
        num = str(day.day)
        d.text((MX - 8, y), num, font=big, fill=p.fg)
        nx = MX + d.textlength(num, font=big) + (24 if compact else 40)
        a, b = GIORNI_LUNGHI[day.weekday()].upper(), MESI[day.month].upper()
        if label:
            b += f" · {label}"
        fs = 64 if not compact else 32 if small else 40
        while max(d.textlength(a, font=chunk(fs)), d.textlength(b, font=chunk(fs))) > R - nx and fs > 20:
            fs -= 2
        top = y + big.size * 0.18
        d.text((nx, top), a, font=chunk(fs), fill=p.fg)
        d.text((nx, top + fs * 1.3), b, font=chunk(fs), fill=p.fg)
        y += int(big.size * 1.08)
    d.line([(MX, y), (R, y)], fill=p.fg, width=2)
    return y


SECTION_H = 96


def _section_head(p: Page, y: int, title: str) -> int:
    """Genre heading at the top of a page: a filled band with the section name."""
    d = p.d
    d.rectangle([MX, y, p.w - MX, y + SECTION_H - 26], fill=p.fg)
    text, f = _fit(d, title, chunk, 46, p.w - 2 * MX - 40, min_size=26)
    d.text((MX + 20, y + (SECTION_H - 26) / 2), text, font=f, fill=p.bg, anchor="lm")
    return y + SECTION_H


def _date_slides(fmt: str, pal: dict, groups: list[tuple[date | None, str | None, list[tuple[Row, Event]]]],
                 bottom_mid: str, month_tag: str, pill: bool, max_slides: int, dense: bool = False,
                 section: str | None = None, pack: bool = False) -> list[Slide]:
    """groups: (date, extra label, rows). One date per slide; a date with too many events
    continues on the next slide; if that makes too many slides, dates are packed together.
    section: a heading (e.g. the genre) repeated on top of every page; pack: always pack dates."""
    w, h, (top, bot) = FORMATS[fmt]
    avail = bot - 70 - (top + 50) - (SECTION_H if section else 0)

    def plan(compact: bool) -> list[list[tuple]]:
        head_h = int((110 if dense else 150 if compact else 300) * 1.08) + (30 if compact else 0)
        rh = lambda r: _row_height(r, pill, dense)  # noqa: E731
        pages, cur, used = [], [], 0
        for day, label, rows in groups:
            i = 0
            while i < len(rows):
                need = head_h + rh(rows[i][0])
                if cur and (not compact or used + need > avail):
                    pages.append(cur)
                    cur, used = [], 0
                part, used = [], used + head_h
                while i < len(rows) and (not part or used + rh(rows[i][0]) <= avail):
                    used += rh(rows[i][0])
                    part.append(rows[i])
                    i += 1
                cur.append((day, label, part))
        if cur:
            pages.append(cur)
        return pages

    pages = plan(dense or pack)
    if len(pages) > max_slides and not dense:
        pages = plan(True)
    if len(pages) > max_slides:
        dropped = sum(len(part) for page in pages[max_slides:] for _, _, part in page)
        import logging
        logging.getLogger("bget").warning("carousel: %d events did not fit in %d slides", dropped, max_slides)
        pages = pages[:max_slides]
    out: list[Slide] = []
    for n, page in enumerate(pages, 1):
        p = _page(fmt, pal, "", bottom_mid, month_tag)
        y = p.top + 50
        if section:
            y = _section_head(p, y, section)
        compact = dense or pack or len(page) > 1
        for day, label, part in page:
            y = _date_header(p, y, day, label, compact, small=dense)
            for row, _ in part:
                y = _draw_dense(p, y, row) if dense else _draw_row(p, y, row, pill)
            y += 30 if compact else 0
        out.append((p.img, [e for _, _, part in page for _, e in part]))
    return out


def _numbered(slides: list[Slide], fmt: str) -> list[Slide]:
    """Write "n/N" in the top edge of every slide after the cover."""
    total = len(slides)
    w, h, (top, bot) = FORMATS[fmt]
    for i, (img, _) in enumerate(slides[1:], 2):
        d = ImageDraw.Draw(img)
        color = WHITE if img.getpixel((5, 5)) == RED else RED
        d.text((w / 2, top), f"{i}/{total}", font=mono(20), fill=color, anchor="ma")
    return slides


def _by_night(events: Iterable[Event]) -> dict[date, list[Event]]:
    """Nights by date; within a date the most important first (ev.rank, set by the collector from
    the venue / room / promoter scores, Filippo 2026-11-17), then by start time."""
    out: dict[date, list[Event]] = defaultdict(list)
    for e in sorted(events, key=lambda e: (-(getattr(e, "rank", None) or 0), e.start)):
        out[e.night].append(e)
    return out


def _alert_group(alerts: Iterable[Event], lo: date, hi: date) -> list[tuple]:
    rows = []
    for e in sorted((e for e in alerts if lo <= e.night <= hi), key=lambda e: e.start):
        r = club_row(e)
        r.time = f"{GIORNI[e.night.weekday()]} {e.night.day}"
        r.sub = " · ".join(x for x in [party_name(e), e.venue.city] if x)
        rows.append((r, e))
    return [(None, "FUORI ZONA", rows)] if rows else []


# ---------- genre sections ----------

# Filippo (2026-11-17): the club nights are split into pages by genre; preserata nights get a
# genre tag only and come after, on white pages so they stay clearly apart. settings.yaml
# `post_genres` can move a genre to another group.
GENRE_GROUPS: dict[str, list[str]] = {
    "HOUSE / ELETTRONICA": ["House", "Tech House", "Deep House", "Afro House", "Melodic Techno", "Techno",
                            "Hard Techno", "Minimal", "Drum & Bass", "Trance", "Hardstyle / Hardcore",
                            "Electronic", "Disco / Funk"],
    "HIP HOP / TRAP": ["Hip Hop / Trap"],
    "COMMERCIALE": ["Commerciale", "Latin / Reggaeton", "Hits / Dance"],
    "ALTRO": [],   # everything else: revival, no genre, ...
}


def genre_group(e: Event, groups: dict[str, list[str]] | None = None) -> str:
    groups = groups or GENRE_GROUPS
    for name, genres in groups.items():
        if e.genre in genres:
            return name
    return list(groups)[-1]


def _sections(fmt: str, events: dict[date, list[Event]], alerts: list[tuple], preserata, groups,
              month_tag: str, budget: int, dense_first: bool = False) -> list[Slide]:
    """Serate by genre group, then the preserata pages (white), then FUORI ZONA. Dates are packed
    within a section; if the post goes past `budget` slides, rows switch to one line each."""
    order = list(groups or GENRE_GROUPS)
    secs: list[tuple[str, dict, list[tuple]]] = []
    for g in order:
        gs = []
        for day, evs in sorted(events.items()):
            rows = [(club_row(e), e) for e in evs if not preserata(e) and genre_group(e, groups) == g]
            if rows:
                gs.append((day, None, rows))
        if gs:
            secs.append((g, CLUB, gs))
    pre = []
    for day, evs in sorted(events.items()):
        rows = [(club_row(e, tag_pill=True), e) for e in evs if preserata(e)]
        if rows:
            pre.append((day, None, rows))
    if pre:
        secs.append(("PRESERATA", OTHER, pre))
    if alerts:
        secs.append(("FUORI ZONA", CLUB, alerts))

    def build(dense: bool) -> list[Slide]:
        out = []
        for title, pal, gs in secs:
            out += _date_slides(fmt, pal, gs, "SOLO DJ & CLUB" if pal is CLUB else "PRESERATA", month_tag,
                                pal is OTHER, 99, dense=dense, pack=True,
                                section=None if title == "FUORI ZONA" else title)
        return out

    slides = build(dense_first)
    if len(slides) > budget and not dense_first:
        slides = build(True)
    if len(slides) > budget:
        import logging
        dropped = sum(len(evs) for _, evs in slides[budget:])
        logging.getLogger("bget").warning("carousel: %d events did not fit in %d slides", dropped, budget)
        slides = slides[:budget]
    return slides


# ---------- the three posts ----------

def monthly(events: Iterable[Event], year: int, month: int, alerts: Iterable[Event] = (), fmt: str = "ig",
           preserata=lambda e: False, genres: dict | None = None) -> list[Slide]:
    nights = {k: v for k, v in _by_night(events).items() if (k.year, k.month) == (year, month)}
    tag = f"{MESI[month][:3].upper()} {year}"
    slides = [(_month_cover(fmt, year, month, {k: len(v) for k, v in nights.items()}), [])]
    first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    slides += _sections(fmt, nights, _alert_group(alerts, first, last), preserata, genres, tag,
                        MAX_SLIDES - 1, dense_first=True)
    return _numbered(slides, fmt)


def weekly(events: Iterable[Event], week_start: date, alerts: Iterable[Event] = (), fmt: str = "ig",
           preserata=lambda e: False, genres: dict | None = None) -> list[Slide]:
    days = [week_start + timedelta(days=i) for i in range(7)]
    nights = {k: v for k, v in _by_night(events).items() if days[0] <= k <= days[-1]}
    end = days[-1]
    span = f"{days[0].day}—{end.day}" if days[0].month == end.month else f"{days[0].day}/{days[0].month}—{end.day}/{end.month}"
    cover = _strip_cover(fmt, CLUB, [span, MESI[end.month][:3].upper()], None, days,
                         {k: len(v) for k, v in nights.items()}, None, f"SETTIMANA {week_start.isocalendar()[1]}",
                         "SOLO DJ & CLUB")
    slides = [(cover, [])] + _sections(fmt, nights, _alert_group(alerts, days[0], days[-1]), preserata, genres,
                                       f"{MESI[end.month][:3].upper()} {end.year}", MAX_SLIDES - 1)
    return _numbered(slides, fmt)


def midweek(others: Iterable[tuple[str, Event]], week_start: date, fmt: str = "ig") -> list[Slide]:
    """Thursday to Sunday of the week: aperitivo / pre-party, comedy, quiz, culture."""
    days = [week_start + timedelta(days=i) for i in range(3, 7)]
    sel = sorted(((k, e) for k, e in others if k in MIDWEEK_KINDS and days[0] <= e.night <= days[-1]),
                 key=lambda x: x[1].start)
    per_day: dict[date, list[tuple[str, Event]]] = defaultdict(list)
    for k, e in sel:
        per_day[e.night].append((k, e))
    labels = {dd: list(dict.fromkeys(MIDWEEK_KINDS[k] for k, _ in v))[:3] for dd, v in per_day.items()}
    kinds = list(dict.fromkeys(MIDWEEK_KINDS[k].lower() for k, _ in sel)) or ["aperitivi", "comedy", "quiz", "cultura"]
    end = days[-1]
    sub = f"{', '.join(kinds).capitalize()}. Dal {days[0].day} al {end.day} {MESI[end.month].lower()}."
    cover = _strip_cover(fmt, OTHER, ["PRIMA", "DELLA", "SERATA"], sub, days,
                         {k: len(v) for k, v in per_day.items()}, labels,
                         "METÀ SETTIMANA", "PRESERATA & ALTRO")
    groups = [(dd, None, [(other_row(k, e), e) for k, e in v]) for dd, v in sorted(per_day.items())]
    slides = [(cover, [])] + _date_slides(fmt, OTHER, groups, "PRESERATA & ALTRO",
                                          f"{MESI[end.month][:3].upper()} {end.year}", True, MAX_SLIDES - 1)
    return _numbered(slides, fmt)


def save(slides: list[Slide], outdir: Path, stem: str) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob(f"{stem}_[0-9][0-9].png"):   # a shorter re-render must not leave stale slides
        old.unlink()
    paths = []
    for i, (s, _) in enumerate(slides, 1):
        p = outdir / f"{stem}_{i:02d}.png"
        s.save(p, optimize=True)
        paths.append(p)
    return paths
