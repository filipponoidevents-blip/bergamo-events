"""Free caption reader: pulls dates, times, venue, @mentions and genres out of a caption with
plain pattern matching. No AI, no API key. Less accurate than the Claude reader, so it scores its
own confidence conservatively and most results wait in the review queue."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from ..config import Config
from ..models import slug
from .instagram_extract import ExtractedArtist, ExtractedEvent, Extraction

MONTHS = {
    "gennaio": 1, "gen": 1, "january": 1, "jan": 1,
    "febbraio": 2, "feb": 2, "february": 2,
    "marzo": 3, "mar": 3, "march": 3,
    "aprile": 4, "apr": 4, "april": 4,
    "maggio": 5, "mag": 5, "may": 5,
    "giugno": 6, "giu": 6, "june": 6, "jun": 6,
    "luglio": 7, "lug": 7, "july": 7, "jul": 7,
    "agosto": 8, "ago": 8, "august": 8, "aug": 8,
    "settembre": 9, "set": 9, "sett": 9, "september": 9, "sep": 9, "sept": 9,
    "ottobre": 10, "ott": 10, "october": 10, "oct": 10,
    "novembre": 11, "nov": 11, "november": 11,
    "dicembre": 12, "dic": 12, "december": 12, "dec": 12,
}
WEEKDAYS = {
    "lunedi": 0, "lunedì": 0, "lun": 0, "monday": 0, "mon": 0,
    "martedi": 1, "martedì": 1, "tuesday": 1, "tue": 1,
    "mercoledi": 2, "mercoledì": 2, "mer": 2, "wednesday": 2, "wed": 2,
    "giovedi": 3, "giovedì": 3, "gio": 3, "thursday": 3, "thu": 3,
    "venerdi": 4, "venerdì": 4, "ven": 4, "friday": 4, "fri": 4,
    "sabato": 5, "sab": 5, "saturday": 5, "sat": 5,
    "domenica": 6, "dom": 6, "sunday": 6, "sun": 6,
}
_MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
_WD_RE = "|".join(sorted(WEEKDAYS, key=len, reverse=True))

RE_DAY_MONTHNAME = re.compile(rf"\b(\d{{1,2}})\s*(?:°|º)?\s*({_MONTH_RE})\.?\b(?:\s+(\d{{4}}))?", re.I)
RE_MONTHNAME_DAY = re.compile(rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
RE_NUMERIC = re.compile(r"(?<![\d:])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d:])")
RE_WEEKDAY_DAY = re.compile(rf"\b({_WD_RE})\.?\s+(\d{{1,2}})\b(?!\s*[:.]\d)", re.I)
RE_WEEKDAY = re.compile(rf"\b({_WD_RE})\b", re.I)
RE_TIME = re.compile(r"\b(?:h\.?\s*|ore\s+|dalle\s+|from\s+|start\s+|doors\s+)?([01]?\d|2[0-3])(?:[:](\d{2})|\s*h\b|[.](\d{2})(?=\s*(?:h\b|-|–|alle|till|until|to\b)))", re.I)
RE_FROM_TO = re.compile(r"\b(?:dalle|from|h)\s*([01]?\d|2[0-3])(?:[:.](\d{2}))?\s*(?:alle|to|till|until|-|–)\s*([01]?\d|2[0-3])(?:[:.](\d{2}))?", re.I)
RE_MENTION = re.compile(r"(?<![\w.])@([A-Za-z0-9_.]{2,30})(?<!\.)")
RE_URL = re.compile(r"https?://\S+")
RECAP = re.compile(r"\b(grazie|thank you|thanks|recap|throwback|last night|ieri sera|che notte|what a night)\b", re.I)
CONCERT = re.compile(r"\b(in concerto|concerto|live band|tour 20\d\d)\b", re.I)
FESTIVAL = re.compile(r"\b(festival|fest)\b", re.I)


def _future(d: date, posted: date) -> bool:
    return posted - timedelta(days=1) <= d <= posted + timedelta(days=300)


def _resolve(day: int, month: int, year: int | None, posted: date) -> date | None:
    for y in ([year] if year else [posted.year, posted.year + 1]):
        try:
            d = date(y, month, day)
        except ValueError:
            return None
        if year or _future(d, posted):
            return d
    return None


RE_BARE_DAY = re.compile(r"\b(?:il|on the|the)\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*[:./]\d)", re.I)


def day_in_posting_month(day: int, posted: date) -> date | None:
    """Rule: a day number without a month is in the posting month. If that day has already
    passed (by more than a day), it is the next month instead, e.g. "sabato 5" posted on 28 Nov."""
    for months_ahead in (0, 1):
        y, m = posted.year + (posted.month + months_ahead - 1) // 12, (posted.month + months_ahead - 1) % 12 + 1
        try:
            d = date(y, m, day)
        except ValueError:
            continue
        if d >= posted - timedelta(days=1):
            return d
    return None


def weekday_in_posting_week(wd: int, posted: date) -> tuple[date, bool]:
    """Rule: a weekday name without a number is in the posting week (Mon-Sun).
    If that day has already passed this week, it means next week; returns (date, rolled_over)."""
    d = posted - timedelta(days=posted.weekday()) + timedelta(days=wd)
    if d < posted:
        return d + timedelta(days=7), True
    return d, False


def find_dates(text: str, posted: date, warnings: list[str] | None = None) -> tuple[list[date], bool]:
    """Return (dates, explicit). explicit is False when the date was inferred from a weekday or
    a word like "stasera". Problems worth a human look are appended to `warnings`."""
    warnings = warnings if warnings is not None else []
    found: list[date] = []

    def add(d: date | None):
        if d and _future(d, posted) and d not in found:
            found.append(d)

    for m in RE_DAY_MONTHNAME.finditer(text):
        add(_resolve(int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3)) if m.group(3) else None, posted))
    for m in RE_MONTHNAME_DAY.finditer(text):
        add(_resolve(int(m.group(2)), MONTHS[m.group(1).lower()], None, posted))
    for m in RE_NUMERIC.finditer(text):
        y = m.group(3)
        y = (2000 + int(y) if len(y) == 2 else int(y)) if y else None
        add(_resolve(int(m.group(1)), int(m.group(2)), y, posted))
    if not found:  # "sabato 22": day number, no month
        for m in RE_WEEKDAY_DAY.finditer(text):
            d = day_in_posting_month(int(m.group(2)), posted)
            if d and d.weekday() != WEEKDAYS[m.group(1).lower()]:
                warnings.append(f"'{m.group(0)}' but {d:%d/%m} is not that weekday")
            add(d)
    if not found:  # "il 22"
        for m in RE_BARE_DAY.finditer(text):
            add(day_in_posting_month(int(m.group(1)), posted))
    if found:
        return sorted(found), True
    low = text.lower()
    if re.search(r"\b(stasera|tonight|questa sera)\b", low):
        return [posted], False
    if re.search(r"\b(domani|tomorrow)\b", low):
        return [posted + timedelta(days=1)], False
    m = RE_WEEKDAY.search(text)
    if m:
        d, rolled = weekday_in_posting_week(WEEKDAYS[m.group(1).lower()], posted)
        if rolled:
            warnings.append(f"'{m.group(1)}' already passed this week, assumed next week")
        return [d], False
    return [], False


def find_times(text: str) -> tuple[str | None, str | None]:
    m = RE_FROM_TO.search(text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2) or '00'}", f"{int(m.group(3)):02d}:{m.group(4) or '00'}"
    for m in RE_TIME.finditer(text):
        h = int(m.group(1))
        if not 7 <= h <= 11:  # nights and day parties; skip morning numbers
            return f"{h:02d}:{m.group(2) or m.group(3) or '00'}", None
    return None, None


def _title(caption: str) -> str:
    for line in caption.splitlines():
        t = RE_URL.sub("", line)
        t = re.sub(r"[#@]\S+", "", t)
        t = re.sub(r"[^\w\s&'’!?.,:/+()-]", "", t).strip(" -–—:|.")
        if len(t) >= 3:
            return t[:80]
    return ""


class RulesExtractor:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.genres = sorted(cfg.settings.get("genres", {}), key=len, reverse=True)
        self.venue_names = [(v["name"], [v["name"], *v.get("aliases", [])]) for v in cfg.venues]
        self.venue_handles = {(v.get("socials") or {}).get("instagram") for v in cfg.venues} - {None}

    def __call__(self, *, caption, image_urls, posted_at: datetime, account, account_venue) -> Extraction:
        caption = caption or ""
        posted = posted_at.date()
        warnings: list[str] = []
        dates, explicit = find_dates(caption, posted, warnings)
        if not dates:
            return Extraction(is_event_announcement=False, confidence=0.0, notes="no date in caption")
        recap = bool(RECAP.search(caption))
        start, end = find_times(caption)

        low = caption.lower()
        genres = []
        for g in self.genres:
            if g in low and not any(g in x for x in genres):
                genres.append(g)
        cap_slug = slug(caption)
        venue = account_venue
        for name, names in self.venue_names:
            if any(len(slug(n)) > 3 and slug(n) in cap_slug for n in names):
                venue = name
                break
        mentions = [h for h in dict.fromkeys(RE_MENTION.findall(caption))
                    if h.lower() != account.lower() and h not in self.venue_handles]
        tickets = next((u for u in RE_URL.findall(caption)
                        if any(k in u for k in ("dice.fm", "xceed", "shotgun", "eventbrite", "ticket", "ra.co"))), None)

        conf = 0.35 + (0.25 if explicit else 0.1) + (0.1 if start else 0) + (0.1 if venue else 0) + (0.1 if genres else 0)
        if len(dates) > 1:
            conf -= 0.1
        conf -= 0.15 * len(warnings)
        if recap:
            conf -= 0.3
        title = _title(caption) or (", ".join(mentions[:2]) if mentions else f"@{account}")
        events = [ExtractedEvent(
            title=title, date=d.isoformat(), start_time=start, end_time=end,
            venue_name=venue, city=None,
            artists=[ExtractedArtist(name=h, instagram=h) for h in mentions[:8]],
            genres=genres[:3], ticket_url=tickets,
            is_concert=bool(CONCERT.search(caption)), is_festival=bool(FESTIVAL.search(caption)),
        ) for d in dates[:4]]
        notes = "read by the free pattern matcher" + ("; looks like a recap" if recap else "") + \
                ("" if explicit else "; date guessed from a weekday name") + \
                ("; artists are the @mentions, check names" if mentions else "") + \
                "".join("; " + w for w in warnings)
        return Extraction(is_event_announcement=conf >= 0.4, events=events,
                          confidence=round(max(0.0, min(conf, 0.95)), 2), notes=notes)
