"""Free caption reader: pulls dates, times, venue, @mentions and genres out of a caption with
plain pattern matching. No AI, no API key. Less accurate than the Claude reader, so it scores its
own confidence conservatively and most results wait in the review queue."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..config import Config
from ..models import slug
from ..text import normalize
from .categories import categorize
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
RE_MONTHNAME_DAY = re.compile(rf"\b({_MONTH_RE})\.?[ \t]+(\d{{1,2}})(?:st|nd|rd|th)?\b(?![:.]\d)", re.I)
RE_NUMERIC = re.compile(r"(?<![\d:])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d:])")
RE_WEEKDAY_DAY = re.compile(rf"\b({_WD_RE})\.?\s+(\d{{1,2}})\b(?!\s*[:.]\d)", re.I)
RE_WEEKDAY = re.compile(rf"\b({_WD_RE})\b", re.I)
RE_MENTION = re.compile(r"(?<![\w.])@([A-Za-z0-9_.]{2,30})(?<!\.)")
RE_URL = re.compile(r"https?://\S+")
RECAP = re.compile(r"\b(grazie|thank you|thanks|recap|throwback|last night|ieri sera|che notte|what a night|scatti|riassunto|foto della serata|photo dump)\b", re.I)
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
    saw_full_date = False  # a day + month was written (even if it is in the past)

    def add(d: date | None):
        if d and _future(d, posted) and d not in found:
            found.append(d)

    for m in RE_DAY_MONTHNAME.finditer(text):
        saw_full_date = True
        add(_resolve(int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3)) if m.group(3) else None, posted))
    for m in RE_MONTHNAME_DAY.finditer(text):
        saw_full_date = True
        add(_resolve(int(m.group(2)), MONTHS[m.group(1).lower()], None, posted))
    for m in RE_NUMERIC.finditer(text):
        saw_full_date = True
        y = m.group(3)
        y = (2000 + int(y) if len(y) == 2 else int(y)) if y else None
        add(_resolve(int(m.group(1)), int(m.group(2)), y, posted))
    if saw_full_date and not found:
        # "mercoledì 04/11" posted on the 16th: the full date is in the past, so this is about a
        # past night (a recap). Don't re-read "mercoledì 04" as next month.
        warnings.append("only past dates written")
        return [], True
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


def find_genres(text: str, scored: list[str]) -> list[str]:
    """Genre words in a caption, as whole words (so "discoteca" is not "disco"). The scored names
    from settings.yaml come first (they drive relevance), then any other genre the caption names."""
    from ..genres import detect_all
    found: list[str] = []
    for g in scored:
        if re.search(rf"(?<![\w]){re.escape(g)}(?![\w])|#{re.escape(g.replace(' ', ''))}", text, re.I) \
                and not any(g in x for x in found):
            found.append(g)
    for label in detect_all(text):
        if not any(detect_all(x)[:1] == [label] for x in found):
            found.append(label.lower())
    return found


# ---- times -----------------------------------------------------------------------------------
# A time is "23:30", "23.30", "21,30", "11.30 pm", "mezzanotte"; a bare hour ("dalle 23 alle 4") only
# counts after a time word. "20.11" is a date, not a time: with . or , the minutes must be 00/15/30/45.
_T = (r"(?:(?P<{n}mid>mezzanotte|midnight)|(?<![\d.,:/])(?P<{n}h>[01]?\d|2[0-4])"
      r"(?:(?P<{n}sep>[:.,])(?P<{n}m>[0-5]\d))?(?![\d/])(?:\s*(?P<{n}ap>am|pm)\b)?)")
_START_WORD = r"(?:dalle\s+ore|dalle|dalla|ore|h\.?|from|start(?:ing)?(?:\s+at)?|opening(?:\s+ore)?|apertura(?:\s+porte)?(?:\s+ore)?|" \
              r"doors(?:\s+open)?(?:\s+at)?|inizio(?:\s+ore)?|alle\s+ore|porte)"
_END_WORD = r"(?:chiusura(?:\s+ore)?|closing(?:\s+at)?|close|end(?:s)?(?:\s+at)?|fino\s+alle(?:\s+ore)?|until|till)"
_JOIN = r"\s*(?:-|–|—|/|alle(?:\s+ore)?|a|to|till|until|fino\s+alle(?:\s+ore)?)\s*"
RE_RANGE = re.compile(rf"(?:\b{_START_WORD}\s*)?{_T.format(n='a')}{_JOIN}(?:h\.?\s*)?{_T.format(n='b')}", re.I)
RE_RANGE_SPACE = re.compile(r"(?<![\d.,:])([01]?\d|2[0-3]):([0-5]\d)\s+([01]?\d|2[0-4]):([0-5]\d)(?![\d])")
RE_START = re.compile(rf"\b{_START_WORD}\s*:?\s*{_T.format(n='a')}", re.I)
RE_END = re.compile(rf"\b{_END_WORD}\s*{_T.format(n='a')}", re.I)
RE_CLOCK = re.compile(rf"(?:^|[⏰⌚🕐🕑🕒🕓🕔🕕🕖🕗🕘🕙🕚🕛🕜🕝🕞🕟🕠🕡🕢🕣🕤🕥🕦🕧—–-]\s*)"
                      rf"(?<![\d.,:])([01]?\d|2[0-3])[:.]([0-5]\d)(?![\d])")
RE_LATE = re.compile(r"\b(?:till\s+(?:very\s+)?late|until\s+late|fino\s+a\s+tarda\s+notte|a\s+tarda\s+notte|"
                     r"fino\s+all'?\s*alba|till\s+dawn|until\s+dawn|till\s+close|fino\s+a\s+chiusura|"
                     r"all\s+night\s+long|after\s*hours?|afterhour)\b", re.I)
# lines about entry prices/conditions or contacts: their times are not the night's times
RE_ENTRY_LINE = re.compile(r"\b(?:entro|ingress[oi]|omaggio|lista|prima\s+de(?:l|lle)|dopo\s+(?:l|le|il)|uscita|"
                           r"iscrizion\w*|drink|prezz\w+|ridott\w*|donna|donne|uomo|uomini|tavol\w+|riservat\w+|"
                           r"whatsapp|prenot\w+|navett\w+)\b|☎|\+39|\d{6,}|\d{3}[ ./]\d{3,}", re.I)


@dataclass
class Times:
    start: str | None = None
    end: str | None = None
    late: bool = False          # "till late", "fino all'alba": ends late, time not given


def _hm(g: dict, n: str) -> tuple[int, int] | None:
    if g.get(f"{n}mid"):
        return 0, 0
    if g.get(f"{n}h") is None:
        return None
    h, m = int(g[f"{n}h"]), int(g.get(f"{n}m") or 0)
    sep, ap = g.get(f"{n}sep"), (g.get(f"{n}ap") or "").lower()
    if sep in (".", ",") and m % 15:
        return None  # "20.11" is a date
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    if h == 24:
        h = 0
    return (h, m) if h < 24 else None


def _night_minutes(hm: tuple[int, int]) -> int:
    """Minutes on the night's clock: 01:30 comes after 23:00 (hours before 07:00 are after midnight)."""
    h, m = hm
    return (h + 24 if h < 7 else h) * 60 + m


def read_times(text: str) -> Times:
    """Start, end and "ends late" of a night, from every line of a caption.

    Several times (a programme: 20:00 cena, 23:00 live, 01:30 dj set) -> start is the earliest, end the
    latest. Lines about entry (\"omaggio donna entro le 23\", \"drink entro l'1\") are skipped.
    """
    starts: list[tuple[int, int]] = []
    ends: list[tuple[int, int]] = []
    late = False
    for line in normalize(text).splitlines():
        if RE_LATE.search(line):
            late = True
        if RE_ENTRY_LINE.search(line):
            continue
        used: list[tuple[int, int]] = []
        for m in RE_RANGE.finditer(line):
            g = m.groupdict()
            a, b = _hm(g, "a"), _hm(g, "b")
            # a bare hour on both sides needs a time word: "dalle 23 alle 4", "h 23 - 03"
            bare = g.get("asep") is None and not g.get("amid") and not g.get("aap")
            if a is None or b is None or (bare and not re.match(rf"\s*{_START_WORD}", m.group(0), re.I)):
                continue
            if _night_minutes(b) <= _night_minutes(a):
                continue
            starts.append(a)
            ends.append(b)
            used.append(m.span())
        for m in RE_RANGE_SPACE.finditer(line):  # "19:00 23:00"
            if any(s <= m.start() < e for s, e in used):
                continue
            a, b = (int(m.group(1)), int(m.group(2))), (int(m.group(3)) % 24, int(m.group(4)))
            if _night_minutes(b) > _night_minutes(a):
                starts.append(a)
                ends.append(b)
                used.append(m.span())

        def free(m):
            return not any(s <= m.start() < e or s < m.end() <= e for s, e in used)

        for m in RE_END.finditer(line):
            hm = _hm(m.groupdict(), "a")
            # a lone "fino alle 23:30" is usually an entry condition; only after-midnight ends count
            if free(m) and hm and hm[0] < 9:
                ends.append(hm)
                used.append(m.span())
        for m in RE_START.finditer(line):
            if free(m) and (hm := _hm(m.groupdict(), "a")):
                starts.append(hm)
                used.append(m.span())
        for m in RE_CLOCK.finditer(line):  # "⏰ 21:00", "🕒00.00", a time at the start of a line
            if free(m) and (m.group(0).rstrip()[-3] == ":" or int(m.group(2)) % 15 == 0):
                starts.append((int(m.group(1)), int(m.group(2))))
    # daytime times only count when nothing in the evening is given (a brunch, a conference)
    evening = [s for s in starts if not 7 <= s[0] < 17]
    starts = evening or starts
    t = Times(late=late)
    if starts:
        s = min(starts, key=_night_minutes)
        t.start = f"{s[0]:02d}:{s[1]:02d}"
        later = [e for e in ends if _night_minutes(e) > _night_minutes(s)]
        if later and not late:
            e = max(later, key=_night_minutes)
            t.end = f"{e[0]:02d}:{e[1]:02d}"
    return t


def find_times(text: str) -> tuple[str | None, str | None]:
    t = read_times(text)
    return t.start, t.end


_FILLER = re.compile(r"\b(?:dalle|dalle ore|ore|alle|fino|h|al|allo|alla|a|il|la|lo|del|della|di|e|from|to|at|"
                     r"the|this|questo|questa|prossimo|prossima|discoteca|club|ti aspettiamo|vi aspettiamo)\b", re.I)


def _strip_date_words(t: str) -> str:
    """What is left of a line once dates, weekdays, times and filler words are taken out."""
    t = re.sub(rf"\b(?:{_WD_RE}|{_MONTH_RE})\b", " ", t, flags=re.I)
    t = re.sub(r"\d{1,2}[:./]\d{2}|\d+", " ", t)
    t = _FILLER.sub(" ", t)
    return re.sub(r"[\W_]+", "", t)


# ---- the night's name ------------------------------------------------------------------------
# Lines that are never the name of the night: prices, lists, addresses, contacts, line-up headers.
_NOT_A_NAME = re.compile(
    r"\b(?:lista|drink|drnk|ingresso|prezz\w*|info|prenot\w*|tavol\w*|biglietti|ticket\w*|prevendit\w*|via|viale|"
    r"piazza|piazzale|largo|corso|line ?-?up|save the date|whatsapp|selezione|aperto a tutti|riservat\w*|"
    r"dress ?code|link in bio|special guest|guest|resident\w*|in console|in consolle|dj set|djset|"
    r"swipe|scorri|maggiorenni|u-?pass|cena|apericena|buffet|menu|menù)\b|€|\+39|\d{6,}", re.I)
# sentence words: a line full of them is chatter ("Ci vediamo il 21 nel club"), not a name
_CHATTER = re.compile(
    r"\b(?:si|ci|vi|ti|è|e'|é|sono|non|che|per|con|nel|nella|del|della|dei|delle|una|un|uno|questo|questa|"
    r"vediamo|aspettiamo|torna|tornano|arriva|siamo|abbiamo|ancora|sempre|già|ora|cosa|fare|"
    r"we|you|are|is|our|your|see|back|with|for|this|that|just|all)\b", re.I)
_GENERIC = re.compile(
    r"^(?:this week|this weekend|one night|tonight|stasera|weekend|save the date|main ?(?:room|area|stage)|"
    r"club ?room|theater room|lab room|sala \w+|room \w+|coming soon|sold out|new date|nuova data|"
    r"see you(?: inside| there)?|no excuses|programma|prossimi eventi|eventi|novit[aà]|info)$", re.I)
_PARTY_WORD = re.compile(r"\b(?:party|night|notte|festa|showcase|presents|presenta|label|"
                         r"rave|session[s]?|club night)\b", re.I)
_RETURNS = re.compile(r"\s+(?:returns|is back|torna|ritorna|is coming)\s*[.!]*$", re.I)
_HANDLE = re.compile(r"(?<![\w@])@?[a-z0-9_]*[a-z0-9][._][a-z0-9_.]*[a-z0-9_]\b")
_EDGE = (rf"oggi|stasera|domani|tonight|torna|ritorna|tornano|returns|is back|presenta|presents|al|alla|allo|a|at|in|"
         rf"da|dal|presso|to|and|e|dalle|alle|ore|h|{_MONTH_RE}|{_WD_RE}")
_TIME_PHRASE = re.compile(
    rf"\b(?:{_START_WORD}|{_END_WORD})?\s*{_T.format(n='x')}(?:{_JOIN}{_T.format(n='y')})?", re.I)


def _clean_name(line: str) -> str:
    t = RE_URL.sub(" ", normalize(line))
    t = re.sub(r"#\S+", " ", t)
    t = _HANDLE.sub(" ", t)
    t = re.sub(r"\s(?:w/|with|@)\s.*$", "", t, flags=re.I)  # "UNIBG PARTY w/ L'OPERÀ": headliner apart
    t = "".join(c for c in t if not c.isalpha() or "LATIN" in unicodedata.name(c, "LATIN"))  # ཐ decorations
    t = re.sub(r"[^\w\s&'!?.,:/+()-]", " ", t)
    # dates and times at the start or end of the line: "21 NOVEMBRE ENTER THE VOID", "OASI · 20.11"
    for _ in range(3):
        t = re.sub(rf"^\W*(?:(?:{_WD_RE})\.?|\d{{1,2}}\s*(?:{_MONTH_RE})\.?(?:\s+\d{{4}})?|"
                   rf"\d{{1,2}}[./]\d{{1,2}}(?:[./]\d{{2,4}})?|(?:{_MONTH_RE})\.?\s+\d{{1,2}}|il|on)\b", " ", t, flags=re.I)
        t = re.sub(rf"\b(?:(?:{_WD_RE})\.?|\d{{1,2}}\s*(?:{_MONTH_RE})\.?(?:\s+\d{{4}})?|"
                   rf"\d{{1,2}}[./]\d{{1,2}}(?:[./]\d{{2,4}})?)\W*$", " ", t, flags=re.I)
        t = _TIME_PHRASE.sub(lambda m: " " if re.search(r"[:.,]\d\d|mezzanotte|dalle|ore|h", m.group(0), re.I)
                             else m.group(0), t)
    t = _RETURNS.sub("", t)
    t = re.sub(r"(?i)\b(?:main|club|lab|theater|theatre|second|back)\s*(?:room|area|stage)\b", " ", t)
    t = re.sub(r"\b(?=[a-z0-9_]*[a-z])[a-z0-9_]*(?:\d|_)[a-z0-9_]*\b", " ", t)  # lowercase handles without the @: il_boffe, illes2cri
    for _ in range(2):  # loose words left at the edges: "TORNA ICON AL", "ALIVE presenta", "dalle alle"
        t = re.sub(rf"^\W*(?:{_EDGE})\b", " ", t.strip(), flags=re.I)
        t = re.sub(rf"\b(?:{_EDGE})\W*$", " ", t.strip(), flags=re.I)
        t = re.sub(r"\s{2,}", " ", t).strip(" -–—:|.,!/&+")
    return t


_GENERIC_WORD = re.compile(r"^(?:party|music|musica|live|night|notte|serata|evento|event|club|dj|program+a|"
                           r"line ?up|info|news|save the date)$", re.I)


def _clean_name_head(line: str) -> str:
    """The line with dates and times stripped but artists left in, to see how it starts."""
    t = normalize(line)
    t = re.sub(r"[^\w\s/'.]", " ", t)
    t = re.sub(rf"(?i)^(?:\s|{_WD_RE}|{_MONTH_RE}|\d{{1,2}}[./]\d{{1,2}}|\d+|dalle|ore|h|[:.])*", "", t)
    return t.strip()


_LINEUP_LINE = re.compile(r"^\W*[🎧🎤🎙🎛]|^[^,+]*\w[^,+]*(?:,|\s\+\s|\sb2b\s)[^,+]*\w", re.I)


def name_candidates(caption: str, venues: list[str] = ()) -> list[tuple[float, str]]:
    out = []
    lines = [ln for ln in normalize(caption).splitlines() if ln.strip()][:10]
    for i, line in enumerate(lines):
        if _NOT_A_NAME.search(line) and not _PARTY_WORD.search(line):
            continue
        if _LINEUP_LINE.match(normalize(line)) and not _PARTY_WORD.search(normalize(line)):
            continue  # "🎧 DANI VESCOVI, LAPO UNDERGROUND": the line-up, not the name
        t = _clean_name(line)
        words = t.split()
        if len(t) < 3 or not words or _GENERIC.match(t) or _NOT_A_NAME.search(t):
            continue
        if re.match(r"(?i)(?:con|w/|feat|ft|dj|by)\b", _clean_name_head(line)):
            continue  # "con cheri.it", "in console ..." -> artists, not a name
        letters = [c for c in t if c.isalpha()]
        if len(letters) < 4 or _GENERIC_WORD.match(t):
            continue
        if t[0].islower():
            continue  # names start with a capital; "take over the night", "fssquah" (a handle without the @)  # a lone lowercase word is a handle written without the @ (fssquah)
        caps = sum(c.isupper() for c in letters) / len(letters) > 0.7
        chatter = len(_CHATTER.findall(t)) * (0.5 if caps else 1.0)
        score = 3.0 - 0.4 * i - 1.2 * chatter - 0.4 * max(0, len(words) - 4)
        if caps:
            score += 1.0
        if _PARTY_WORD.search(t):
            score += 1.5
        elif re.search(r"\sw/\s|\sx\s", line, re.I):
            score += 0.5
        if t.endswith((".", "!", "?")) and len(words) > 3:
            score -= 1.0
        if any(len(v) > 3 and re.search(rf"(?<!\w){re.escape(v)}(?!\w)", t, re.I) for v in venues):
            score -= 1.5  # "LOFT - HiFi Bar & Premium Club" is the venue's tagline
        out.append((score, t[:60]))
    return out


def party_title(caption: str, venues: list[str] = ()) -> str:
    """The night's own name from a caption, or "" when the post doesn't give one."""
    best = max(name_candidates(caption, venues), default=(0, ""))
    return best[1] if best[0] >= 1.0 else ""


def _title(caption: str) -> str:  # kept for older callers
    return party_title(caption)


# ---- clues for serata / preserata -------------------------------------------------------------
RE_PAID = re.compile(r"\d+\s*€|€\s*\d+|\d+\s*euro\b|\blista\b|drink incluso|dr\*?nk incluso|\bticket|biglietti|"
                     r"prevendit|\bu-?pass\b|xceed|dice\.fm|shotgun|tavol[oi]|\bprive\b|privè|privé|superprive", re.I)
RE_FREE = re.compile(r"ingresso\s+(?:libero|gratuito|gratis|free)|entrata\s+libera|free\s+entry|"
                     r"ingresso\s+(?:a\s+)?offerta|free\s+entrance", re.I)
RE_FREE_FOR_SOME = re.compile(r"(?:libero|gratuito|gratis|free|omaggio)\s+(?:per\s+)?(?:a\s+tutte\s+le\s+)?"
                              r"(?:donn|ragazz|entro|fino|prima|con\s+c\.?o|dalle)", re.I)
RE_SAYS_PRESERATA = re.compile(r"\bpre[- ]?serata\b|\baperitivo\b|\bapericena\b|\bhappy hour\b|\bafter ?work\b", re.I)
RE_SAYS_SERATA = re.compile(r"\bafter ?hours?\b|\bafterhour\b|fino all'?\s*alba|till dawn|all night long", re.I)
DAY_PARTY = re.compile(r"day ?party|pool ?party|open ?air|boat ?party|daytime|afternoon party|"
                       r"sunset|tea ?dance|matin[eé]e", re.I)


def entry_hint(text: str) -> str | None:
    """"paid" when the post talks about lists, prices or tickets; "free" when it says free entry
    for everyone ("ingresso gratuito a tutte le donne entro le 22" is not free entry)."""
    text = normalize(text)
    if RE_PAID.search(text):
        return "paid"
    if RE_FREE.search(text) and not RE_FREE_FOR_SOME.search(text):
        return "free"
    return None


def fascia_hint(text: str) -> str | None:
    """What the post itself says: "aperitivo con dj set" -> preserata, "after hour" -> serata."""
    text = normalize(text)
    if RE_SAYS_SERATA.search(text):
        return "serata"
    if RE_SAYS_PRESERATA.search(text):
        return "preserata"
    return None


def is_daytime(t: Times) -> bool:
    """A conference 09:30-17:30 or a brunch 11-15 is not a night out."""
    if not t.start:
        return False
    h = int(t.start[:2])
    end_h = int(t.end[:2]) if t.end else None
    return 7 <= h < 17 and (end_h is None or 7 < end_h <= 20)


class RulesExtractor:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.genres = sorted(cfg.settings.get("genres", {}), key=len, reverse=True)
        self.venue_names = [(v["name"], [v["name"], *v.get("aliases", [])]) for v in cfg.venues]
        self.venue_handles = {(v.get("socials") or {}).get("instagram") for v in cfg.venues} - {None}
        self.venue_kinds = {v["name"]: v.get("kind") for v in cfg.venues}
        self.handle_venue = {(v.get("socials") or {}).get("instagram").lower(): v["name"] for v in cfg.venues
                             if (v.get("socials") or {}).get("instagram")}
        # name used as the night's name: `party_name:` in promoters.yaml, else the promoter's name
        self.promoter_handles = {p["instagram"].lower(): p.get("party_name") or p["name"]
                                 for p in (cfg.promoters or []) if p.get("instagram")}

    def __call__(self, *, caption, image_urls, posted_at: datetime, account, account_venue) -> Extraction:
        caption = normalize(caption)
        posted = posted_at.date()
        warnings: list[str] = []
        dates, explicit = find_dates(caption, posted, warnings)
        if not dates:
            return Extraction(is_event_announcement=False, confidence=0.0, notes="no date in caption")
        recap = bool(RECAP.search(caption))
        times = read_times(caption)
        start, end = times.start, times.end

        genres = find_genres(caption, self.genres)
        cap_slug = slug(caption)
        venue = account_venue
        for name, names in self.venue_names:
            if any(len(slug(n)) > 3 and slug(n) in cap_slug for n in names):
                venue = name
                break
        else:  # the venue tagged by its handle, often without the @: "OASI returns ... vibetheclub"
            hit = sorted((m.start(), name) for h, name in self.handle_venue.items() if h != account.lower()
                         if (m := re.search(rf"(?<![\w.]){re.escape(h)}(?![\w])", caption, re.I)))
            if hit and not account_venue:
                venue = hit[0][1]
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
        # no name of its own -> the venue (the calendar then shows just "[Genre] Venue w/ Headliner")
        names = [n for v, ns in self.venue_names if v in (venue, account_venue) for n in ns]
        # a known promoter tagged in the post names the night when the post has no name of its own
        # (handles often come through the API without the @: "dalle 21:00 con cheri.it")
        # the first one written wins: "MAIN ROOM: oasi.night / THEATER ROOM: upside.down.night" -> OASI
        found = sorted((m.start(), name) for h, name in self.promoter_handles.items() if h != account.lower()
                       if (m := re.search(rf"(?<![\w.]){re.escape(h)}(?![\w])", caption, re.I)))
        promoter = found[0][1] if found else None
        mentions = [h for h in mentions if h.lower() not in self.promoter_handles]
        # in a venue's own post, the promoter it tags is the name of the night (Filippo, 2026-11-17)
        # who posted decides first (Filippo, 2026-11-17): a promoter posting its own night -> the promoter's
        # name ("POP UP PARTY" posted by The Box at Tassino -> The Box); a venue's post -> the promoter it tags
        own = self.promoter_handles.get(account.lower()) if not account_venue else None
        title = own or (promoter if account_venue else None) or party_title(caption, names) or promoter or venue or account_venue or (mentions[0] if mentions else f"@{account}")
        kind = categorize(caption, self.venue_kinds.get(venue))
        if kind == "dj_club" and is_daytime(times) and not DAY_PARTY.search(caption):
            kind = "other"
        hint = fascia_hint(caption)
        events = [ExtractedEvent(
            title=title, date=d.isoformat(), start_time=start, end_time=end, late_end=times.late,
            entry=entry_hint(caption), fascia=hint, venue_name=venue, city=None,
            artists=[ExtractedArtist(name=h, instagram=h) for h in mentions[:8]],
            genres=genres[:3], ticket_url=tickets,
            is_concert=bool(CONCERT.search(caption)), is_festival=bool(FESTIVAL.search(caption)),
            kind=kind,
        ) for d in dates[:4]]
        notes = "read by the free pattern matcher" + ("; looks like a recap" if recap else "") + \
                ("" if explicit else "; date guessed from a weekday name") + \
                ("; artists are the @mentions, check names" if mentions else "") + \
                "".join("; " + w for w in warnings)
        return Extraction(is_event_announcement=conf >= 0.4, events=events,
                          confidence=round(max(0.0, min(conf, 0.95)), 2), notes=notes)


RE_DATE_LINE = re.compile(rf"^\W*(?:{_WD_RE})\b|^\W*\d{{1,2}}\s*(?:{_MONTH_RE})\b|^\W*\d{{1,2}}[./]\d{{1,2}}\b", re.I)


class ListingExtractor(RulesExtractor):
    """For aggregator accounts that post a weekly/monthly agenda as an image (read via OCR).

    Reads the text top to bottom: a short line starting with a day ("VENERDÌ 21", "21/11",
    "SABATO") opens a new day; within a day, each line that names a known venue becomes an
    event at that venue on that day. The event title is the rest of the line, or the next
    line if the venue stands alone. Everything goes to review (confidence below auto-approve),
    since OCR text is noisy.
    """

    confidence = 0.6

    def __call__(self, *, caption, image_urls, posted_at: datetime, account, account_venue) -> Extraction:
        posted = posted_at.date()
        lines = [ln.strip() for ln in (caption or "").splitlines() if ln.strip()]
        events: list[ExtractedEvent] = []
        day: date | None = None
        for i, line in enumerate(lines):
            if len(line) <= 40 and RE_DATE_LINE.search(line):
                ds, _ = find_dates(line, posted)
                if ds:
                    day = ds[0]
                    continue
            if not day:
                continue
            venue = self._venue_in(line)
            if not venue:
                continue
            title = self._strip_venue(line, venue)
            if len(title) < 3 and i + 1 < len(lines) and not self._venue_in(lines[i + 1]) \
                    and not RE_DATE_LINE.search(lines[i + 1]):
                title = lines[i + 1]
            title = re.sub(r"\s{2,}", " ", re.sub(r"[^\w\s&'’!?.,:/+()-]", " ", title)).strip(" -–—:|.,")
            start, end = find_times(line + " " + (lines[i + 1] if i + 1 < len(lines) else ""))
            low = line.lower()
            events.append(ExtractedEvent(
                title=title or venue, date=day.isoformat(), start_time=start, end_time=end,
                venue_name=venue, genres=find_genres(line, self.genres)[:2],
                kind=categorize(line + " " + title, self.venue_kinds.get(venue))))
        if not events:
            return Extraction(is_event_announcement=False, confidence=0.0, notes="no day + venue pairs found")
        return Extraction(is_event_announcement=True, events=events, confidence=self.confidence,
                          notes=f"from @{account}'s agenda image (OCR); check title and line-up")

    def _venue_in(self, line: str) -> str | None:
        s = slug(line)
        for name, names in self.venue_names:
            if any(len(slug(n)) > 3 and re.search(rf"(^|-){re.escape(slug(n))}(-|$)", s) for n in names):
                return name
        return None

    def _strip_venue(self, line: str, venue: str) -> str:
        for name, names in self.venue_names:
            if name == venue:
                for n in sorted(names, key=len, reverse=True):
                    line = re.sub(re.escape(n), " ", line, flags=re.I)
        return line.strip()
