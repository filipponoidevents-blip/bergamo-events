"""Legge un testo (didascalia Instagram, articolo) e ne ricava gli eventi. Gratis: solo regole, niente AI.

Ogni evento letto è un dizionario "grezzo": date, start, end, venue, room, party, guests, genre, type,
price, entry, confidence. La pipeline poi lo unisce agli altri, lo classifica e gli dà un voto.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .city import City, clean_name
from .util import contains, norm, plain

MONTHS = {"gen": 1, "gennaio": 1, "jan": 1, "january": 1, "feb": 2, "febbraio": 2, "february": 2, "marzo": 3, "march": 3,
          "apr": 4, "aprile": 4, "april": 4, "mag": 5, "maggio": 5, "may": 5, "giu": 6, "giugno": 6, "jun": 6, "june": 6,
          "lug": 7, "luglio": 7, "jul": 7, "july": 7, "ago": 8, "agosto": 8, "aug": 8, "august": 8, "set": 9, "sett": 9,
          "settembre": 9, "sep": 9, "sept": 9, "september": 9, "ott": 10, "ottobre": 10, "oct": 10, "october": 10,
          "nov": 11, "novembre": 11, "november": 11, "dic": 12, "dicembre": 12, "dec": 12, "december": 12}
WEEKDAYS = {"lunedi": 0, "monday": 0, "mon": 0, "lun": 0, "martedi": 1, "tuesday": 1, "tue": 1, "mercoledi": 2, "wednesday": 2,
            "wed": 2, "mer": 2, "giovedi": 3, "thursday": 3, "thu": 3, "gio": 3, "venerdi": 4, "friday": 4, "fri": 4, "ven": 4,
            "sabato": 5, "saturday": 5, "sat": 5, "sab": 5, "domenica": 6, "sunday": 6, "sun": 6, "dom": 6}
MONTH_RE = "|".join(sorted(MONTHS, key=len, reverse=True))
WD_RE = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
WD_LONG = "|".join(k for k in WEEKDAYS if len(k) > 3)

D_MONTH = re.compile(rf"(?<![\d:.,])(\d{{1,2}})(?:°|º|st|nd|rd|th)?\.?\s*(?:di\s+)?({MONTH_RE})\b\.?(?:\s+(20\d{{2}}))?", re.I)
MONTH_D = re.compile(rf"\b({MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)\b", re.I)
D_NUM = re.compile(r"(?<![\d:.,/€])(\d{1,2})[./](\d{1,2})(?:[./](20\d{2}|\d{2}))?(?![\d:.,/€])")
RANGE_WORDS = re.compile(r"\b(?:dal|from)\s+\d{1,2}.{0,25}?\b(?:al|to|fino al)\s+\d{1,2}", re.I)
PROGRAMME_LINE = re.compile(rf"^\W*(?:{WD_RE})\w*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:[./](\d{{1,2}}))?\b[\s:·|>=-]*(.+)$", re.I)
EVERY = re.compile(rf"\b(?:every|ogni|tutti i|tutte le)\s+({WD_LONG})\b[\s:·|-]*(.*)$", re.I)
REL_DAY = re.compile(rf"\b(?:this|questo|questa|quest'|next|prossimo)?\s*({WD_LONG})\b", re.I)
TONIGHT = re.compile(r"\b(stasera|stanotte|tonight|questa sera|questa notte|oggi)\b", re.I)
TOMORROW = re.compile(r"\b(domani|tomorrow)\b", re.I)

T = r"(\d{1,2})(?:[:.,h](\d{2}))?\s*(am|pm)?"
TIME_RANGE = re.compile(rf"(?<![\d/€]){T}\s*(?:-|–|—|>|/+|\balle\b|\ball[e']?\b|\bto\b|\btill\b|\buntil\b|\bfino alle\b|\ba\b|\s)\s*(?:ore\s*)?{T}(?![\d/€])", re.I)
START = re.compile(rf"(?:dalle(?:\s+ore)?|from|start(?:ing)?(?:\s+at)?|inizio(?:\s+(?:alle|ore))?|opening(?:\s+ore)?|apertura(?:\s+porte)?(?:\s+(?:alle|ore))?|doors?(?:\s+open)?(?:\s+at)?|\bore\b|\bh\.?|⏰|🕒|🕙|🕚|🕛|🕘|🕖|🕗|🕐)\s*:?\s*{T}", re.I)
END = re.compile(rf"(?:fino alle(?:\s+ore)?|chiusura(?:\s+ore)?|till|until|closing(?:\s+at)?)\s*:?\s*{T}", re.I)
LATE = re.compile(r"till late|fino a tard|notte fonda|until late|fino all'alba|till dawn|all night|tutta la notte", re.I)
MIDNIGHT_START = re.compile(r"\b(?:da|dalla|from|dopo la|after)\s+(?:mezzanotte|midnight)\b", re.I)

FREE = re.compile(r"ingresso\s+(?:libero|gratuito|gratis|free)|free\s+entry|entrata\s+(?:libera|gratuita)|free\s+dj\s*set|free\s+party|ingresso\s+omaggio(?!\s+donn)", re.I)
PAID = re.compile(r"€|\beuro\b|\blista\b|\bticket|\bbigliett|prevendit|drink\s+inclus|dr\*nk|consumazione|\btavol[io]\b|\bupass\b|u-pass|xceed|ticketsms|early\s*bird|guest\s*list", re.I)
PRICE = re.compile(r"(?:€\s*(\d{1,3})(?:[.,]\d{2})?|(\d{1,3})(?:[.,]\d{2})?\s*(?:€|euro\b))", re.I)
CANCELLED = re.compile(r"cancellat|annullat|rinviat|posticipat|sold\s*out\s*-\s*annull", re.I)
RECAP = re.compile(r"\b(?:grazie a tutti|thank you|thanks to|che serata|ieri sera|ieri notte|last night|recap|aftermovie|rivivi|foto della serata|le foto di)\b", re.I)
URL = re.compile(r"https?://\S+")
ADDRESS = re.compile(r"\b(?:via|viale|piazza|piazzale|largo|corso|strada|localit[aà]|vicolo)\s+[A-ZÀ-Ú][^\n,;–—|()]{2,40}?(?:[, ]\s*n?\.?\s*\d{1,4}[a-z]?)?(?=[\s,;.\-–—|()]|$)", re.I)

GENRES = [  # (etichetta, parole che la indicano) in ordine: vince la più specifica
    ("Hard Techno", r"hard\s*techno|\bhardcore\b|hardstyle|schranz|\brave\b"), ("Tech House", r"tech\s*-?house"), ("Afro House", r"afro\s*house"),
    ("Melodic Techno", r"\bmelodic\b"), ("Deep House", r"deep\s*house"), ("Techno", r"\btechno\b"), ("House", r"\bhouse\b(?!\s*(?:of|music\s+incontra))"),
    ("Drum And Bass", r"drum\s*(?:and|&|n|'n')\s*bass|\bdnb\b|jungle"), ("Trance", r"\btrance\b|\bpsy"), ("Elettronica", r"\belettronic|\belectronic|\bedm\b|\bminimal\b"),
    ("Trap", r"\btrap\b"), ("Hip Hop", r"hip\s*-?hop|\brap\b|urban|r&b|rnb"), ("Reggaeton", r"reggaeton|regueton|reggaetton|dembow"),
    ("Latino", r"\blatin[oa]?\b|salsa|bachata|kizomba"), ("Afro", r"afro\s*beat|\bafro\b|amapiano"),
    ("Revival", r"anni\s*['’]?\s*(?:60|70|80|90|2000|00)|\brevival\b|\b(?:80|90|2000)s\b|\b(?:80|90)\s*/\s*(?:90|2000)\b"),
    ("Commerciale", r"commercial[e]?|\bhit[s]?\b|happy\s*music|pop\s*dance|\bdance\b|mainstream"), ("Disco", r"\bdisco\s*(?:music|funk)|\bfunk\b|\bfunky\b|italo\s*disco"),
    ("Indie", r"\bindie\b"), ("Rock", r"\brock\b|\bmetal\b|\bpunk\b"), ("Jazz", r"\bjazz\b|\bblues\b|swing"), ("Pop", r"\bpop\b(?!\s*-?up)"),
]
TYPES = [  # tipo di intrattenimento
    ("karaoke", r"karaoke"), ("quiz", r"\bquiz\b|trivia|pub\s*quiz|tombola|bingo|giochi\s+da\s+tavolo|beer\s*pong|torneo"),
    ("comedy", r"stand\s*-?up|comedy|cabaret|comic[oi]"), ("dinner show", r"dinner\s*show|cena\s+spettacolo|cena\s+con\s+(?:musica|spettacolo|delitto)"),
    ("festival", r"\bfestival\b|open\s*air\b"),
    ("culture", r"\bmostra\b|vernissage|presentazione|\blibro\b|workshop|laboratorio|\bcorso\b|mercatino|\bmarket\b|proiezione|\bfilm\b|cineforum|teatro|spettacolo teatrale|conferenza|\btalk\b|incontro con|degustazione|yoga|pattinaggio|\bbambini\b|aperitivo in lingua|convegno"),
    ("sport", r"maxischermo|champions\s+league|\bin\s+diretta\b"),
    ("dj set", r"dj\s*-?set|\bdj[s']?\b|consol+e|line\s*-?up|special\s+guest|b2b|behind\s+the\s+decks|dancefloor|clubbing"),
    ("live", r"\blive\b(?!\s*(?:club|set))|concerto|\bband\b|unplugged|acustic|tribute|cover\s*band|jam\s*session|cantautor"),
    ("dj set", r"\bparty\b|\bguest\b|resident|serata|\bnight\b|discoteca|si\s+balla|selecta|selector"),
    ("aperitivo", r"aperitiv|apericena|after\s*work|brunch|pomeriggio"),
]
ROOM = re.compile(r"\b(main\s*room|main\s*area|main\s*stage|club\s*room|lab\s*room|theat(?:er|re)\s*room|second\s*room|sala\s*(?:1|2|due|uno|principale|priv[eèé])|priv[eèé]|garden|terrazza|rooftop|bunker|elysium|el\s+cielo|room\s*[12])\b", re.I)
GUEST_CUE = re.compile(r"(?<![\w#@.])(?:special\s+guests?\s*[:\-–|]*|guests?\s*[:\-–|]|in\s+consol+e\s*[:\-–|]*|alla\s+consol+e\s*[:\-–|]*|line\s*-?up\s*[:\-–|]*|dj\s*-?set\s+(?:by|di|con)\s|dj\s*-?set\s*[:\-–|]|djs?\s*[:\-–|]|w/|feat\.?\s|ospit[ei]\s*[:\-–|])\s*(.*)$", re.I)
DECKS = re.compile(r"\b(?:with|con)\s+(.{3,70}?)\s+(?:behind the decks|in consol+e|alla consol+e)", re.I)
NOT_NAME = re.compile(r"^(?:il|la|i|le|un|una|e|ed|con|dalle|ore|per|info|free|ingresso|entry|prenot|tavol|drink|lista|ticket|from|till|and|vi\s|ci\s|si\s|music|musica|sarà|will|is|are|returns?|fino|opening|chiusura|prezzi|location)\b|\d[:.]\d{2}|€|http|#", re.I)
NAME_PREFIX = re.compile(r"^(?:djs?|voice|vocalist|mc|live)\s*[:.]?\s+", re.I)
HANDLE = re.compile(r"(?<![\w./@-])@?([a-z0-9][a-z0-9_.]{2,29})(?![\w@/-])")


def _year_for(day: int, month: int, ref: date, year: int | None = None) -> date | None:
    try:
        if year:
            return date(year if year > 99 else 2000 + year, month, day)
        d = date(ref.year, month, day)
        if d < ref - timedelta(days=60):
            d = date(ref.year + 1, month, day)
        return d
    except ValueError:
        return None


def find_dates(text: str, ref: date) -> list[tuple[date, int, bool]]:
    """Le date scritte nel testo: (data, posizione, esplicita). Le stagioni "dal 20 al 30" non contano."""
    spans = [m.span() for m in RANGE_WORDS.finditer(text)]
    inside = lambda pos: any(a <= pos < b + 30 for a, b in spans)
    out = []
    for m in D_MONTH.finditer(text):
        d = _year_for(int(m.group(1)), MONTHS[m.group(2).lower()], ref, int(m.group(3)) if m.group(3) else None)
        if d and not inside(m.start()):
            out.append((d, m.start(), True))
    for m in MONTH_D.finditer(text):
        d = _year_for(int(m.group(2)), MONTHS[m.group(1).lower()], ref)
        if d and not inside(m.start()):
            out.append((d, m.start(), True))
    for m in D_NUM.finditer(text):
        dd, mm = int(m.group(1)), int(m.group(2))
        before = text[max(0, m.start() - 12):m.start()].lower()
        if not (1 <= dd <= 31 and 1 <= mm <= 12) or re.search(r"(ore|h|dalle|alle|tel|cell)\W*$", before) or inside(m.start()):
            continue
        if m.group(2) in ("00", "15", "30", "45") and not m.group(3):
            continue  # è un orario (21.30), non una data
        d = _year_for(dd, mm, ref, int(m.group(3)) if m.group(3) else None)
        if d:
            out.append((d, m.start(), True))
    seen, uniq = set(), []
    for d, pos, ex in sorted(out, key=lambda x: x[1]):
        if d not in seen:
            seen.add(d)
            uniq.append((d, pos, ex))
    return uniq


def relative_date(text: str, ref: date) -> date | None:
    """'questo venerdì', 'stasera', 'domani': il primo giorno giusto a partire dal giorno del post."""
    if TONIGHT.search(text):
        return ref
    if TOMORROW.search(text):
        return ref + timedelta(days=1)
    m = REL_DAY.search(plain(text))
    if m:
        wd = WEEKDAYS[m.group(1).lower()]
        return ref + timedelta(days=(wd - ref.weekday()) % 7)
    return None


def _clock(h, m, ampm=None) -> str | None:
    h, m = int(h), int(m or 0)
    if ampm:
        h = h % 12 + (12 if ampm.lower() == "pm" else 0)
    return f"{h:02d}:{m:02d}" if h < 24 and m < 60 else (f"00:{m:02d}" if h == 24 and m < 60 else None)


def find_times(text: str) -> dict:
    """Ora di inizio e di fine. Le righe con i prezzi ("prima dell'1:00 €15") non contano."""
    lines = [l for l in text.splitlines() if not re.search(r"€|euro|omaggio|before|after|prima dell|dopo l|entro l|fino a mezzanotte", l, re.I) or re.search(r"dalle ore|opening|chiusura", l, re.I)]
    body = "\n".join(lines)
    out = {"start": None, "end": None, "late_end": bool(LATE.search(text))}
    op = re.search(rf"(?:opening|apertura(?:\s+porte)?|doors?(?:\s+open)?)\s*(?:ore|alle|at|h\.?)?\s*:?\s*{T}", body, re.I)
    cl = re.search(rf"(?:chiusura|closing)\s*(?:ore|alle|at|h\.?)?\s*:?\s*{T}", body, re.I)
    if op and cl and _clock(*op.groups()) and _clock(*cl.groups()):
        out["start"], out["end"] = _clock(*op.groups()), _clock(*cl.groups())
        return out
    for m in TIME_RANGE.finditer(body):
        h1, m1, a1, h2, m2, a2 = m.groups()
        if (m1 is None and m2 is None and not a1 and not a2):
            pre = body[max(0, m.start() - 14):m.start()].lower()
            if not re.search(r"(dalle|ore|from|h\.?|orari[o]?)\W*$", pre):
                continue
        if (m1 and m1 not in ("00", "15", "30", "45")) or (m2 and m2 not in ("00", "15", "30", "45")):
            continue
        s, e = _clock(h1, m1, a1), _clock(h2, m2, a2)
        if s and e and s != e:
            out["start"], out["end"] = s, e
            break
    if not out["start"]:
        for m in START.finditer(body):
            if m.group(2) and m.group(2) not in ("00", "15", "30", "45"):
                continue
            c = _clock(*m.groups())
            if c and (m.group(2) or int(m.group(1)) <= 24):
                out["start"] = c
                break
    if not out["start"] and MIDNIGHT_START.search(body):
        out["start"] = "00:00"
    if not out["end"]:
        m = END.search(body)
        if m and (m.group(2) or m.group(3)):
            out["end"] = _clock(*m.groups())
    return out


def find_genre(text: str) -> str | None:
    t = plain(text).lower()
    found = []
    for label, pat in GENRES:
        m = re.search(pat, t)
        if m:
            found.append((m.start(), label))
    if not found:
        return None
    found.sort()
    labels = []
    for _, l in found:
        if l not in labels and not any(l != x and l in x for x in labels):
            labels.append(l)
    return " / ".join(labels[:2])


def find_type(text: str) -> str | None:
    t = plain(text).lower()
    return next((label for label, pat in TYPES if re.search(pat, t)), None)


def find_price(text: str) -> tuple[str | None, int | None]:
    prices = [int(a or b) for a, b in PRICE.findall(text) if 0 < int(a or b) < 300]
    if FREE.search(text) and not prices:
        return "gratis", 0
    if prices:
        return "a pagamento", min(prices)
    if PAID.search(text):
        return "a pagamento", None
    return ("gratis", 0) if FREE.search(text) else (None, None)


def split_names(chunk: str) -> list[str]:
    """'MAD DOG + GRAVEDGR + FLYMEON' -> tre nomi."""
    chunk = re.split(r"[.!?\n]|\s[-–—]\s|\bdalle\b|\bfrom\b|\bore\b", chunk)[0]
    parts = re.split(r"\s*(?:\+|,|/|&|\bb2b\b|\be\b|\band\b|\|)\s*", chunk, flags=re.I)
    out = []
    for p in parts:
        p = NAME_PREFIX.sub("", clean_name(p).strip(" :-")).strip(" :-")
        handle_like = re.fullmatch(r"[a-z0-9]+(?:[._][a-z0-9]+)+_?", p) is not None
        proper = all(w[0].isupper() or w[0].isdigit() or w.lower() in ("de", "di", "da", "van", "von", "the", "of") for w in p.split()) if p else False
        if 2 <= len(p) <= 32 and len(p.split()) <= 4 and not NOT_NAME.search(p) and not p.isdigit() and (handle_like or proper):
            out.append(p)
    return out


class Reader:
    def __init__(self, city: City):
        self.city = city
        self._account = None

    # ---- pezzi ------------------------------------------------------------------
    def handles(self, text: str) -> list[str]:
        """I profili citati. L'API toglie la @, quindi vale ogni parola che è un profilo noto o ne ha la forma."""
        known = self.city.registry["accounts"]
        out = []
        for m in HANDLE.finditer(URL.sub(" ", text)):
            raw = m.group(0)
            h = m.group(1).strip(".").lower()
            shaped = ("_" in h or "." in h) and re.search(r"[a-z]{3}", h) and not re.fullmatch(r"[\d.]+", h) and not re.search(r"\.(?:it|com|me|net|org|eu)$", h)
            if raw.startswith("@") or h in known or self.city.venue_by_handle(h) or self.city.promoter(h) or (shaped and m.group(1) == m.group(1).lower()):
                if h not in out:
                    out.append(h)
        return out

    def guests(self, text: str, skip: set[str]) -> list[dict]:
        """Ospiti e DJ: nomi dopo 'special guest', 'in consolle'... e profili taggati che non sono locali o promoter."""
        found: list[dict] = []
        def add(name, handle=None, cue=False):
            key = norm(name)
            if key and key not in skip and not any(norm(g["name"]) == key for g in found):
                found.append({"name": name, "instagram": handle, "level": self.city.artist_level(name), "cue": cue})
        text = re.sub(r"#\S+", " ", text)
        for m in DECKS.finditer(plain(text)):
            for n in split_names(m.group(1)):
                add(n, n.lower() if n == n.lower() else None, cue=True)
        for m in re.finditer(r"(?:^|[^\w#@])DJ\s+(?!SET\b|-?SET\b)([A-Z][A-Z .'&0-9]{2,60})", plain(text)):
            for n in split_names(re.sub(r"\bDJ\s+", "", m.group(1))):
                add(n.title() if n.isupper() and len(n) > 4 else n, None, cue=True)
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = GUEST_CUE.search(plain(line))
            if not m:
                continue
            rest = m.group(1).strip()
            chunks = [rest] if rest else []
            if not rest:
                for nxt in lines[i + 1:i + 5]:
                    if not nxt.strip() or ROOM.search(nxt) or PAID.search(nxt):
                        break
                    chunks.append(nxt)
            for c in chunks:
                for n in split_names(c):
                    add(n, n.lower() if re.fullmatch(r"[a-z0-9._]+", n) else None, cue=True)
        tn = norm(text)
        for a in self.city.known_artists_in(tn):
            add(a["name"])
        for h in self.handles(text):
            acc = self.city.account(h)
            if self.city.venue_by_handle(h) or self.city.promoter(h) or acc.get("kind") in ("venue", "promoter", "media", "ignore"):
                continue
            if acc.get("kind") == "artist" or acc.get("followers"):
                add(clean_name(acc.get("name") or h), h)
            elif not any(g.get("instagram") == h for g in found) and h != (self._account or ""):
                add(h, h)
        for g in found:
            g["followers"] = self.city.followers(g["instagram"]) if g.get("instagram") else None
        return found

    def title_line(self, text: str) -> str | None:
        """Il nome della serata quando nessun promoter è riconosciuto: la prima riga corta scritta in maiuscolo."""
        for line in text.splitlines()[:6]:
            l = re.sub(r"^\d{1,2}\s+", "", clean_name(line)).strip()
            if line.rstrip().endswith(":"):
                continue
            if not (3 <= len(l) <= 40) or D_MONTH.search(l) or D_NUM.search(l) or re.search(rf"\b(?:{WD_LONG})\b", plain(l).lower()):
                continue
            letters = [c for c in l if c.isalpha()]
            if letters and sum(c.isupper() for c in letters) / len(letters) > 0.8 and len(l.split()) <= 5 and not PAID.search(l) and not FREE.search(l) \
                    and not ROOM.fullmatch(l.strip()) and not re.search(r"ingresso|entrata|special guest|dj\s*set|line\s*-?up|save the date|info|prenot", l, re.I):
                return l.title() if len(l) > 4 else l
        return None

    # ---- lettura ------------------------------------------------------------------
    def read(self, text: str, *, when: datetime, account: str | None = None, source: str = "instagram", url: str | None = None) -> list[dict]:
        if not text or not text.strip():
            return []
        text = plain(text)
        self._account = (account or "").lower()
        ref = when.date()
        if CANCELLED.search(text):
            return []
        acc_venue = self.city.venue_by_handle(account)
        acc_prom = self.city.promoter(account)
        programme = self._programme(text, ref, acc_venue, acc_prom, account, source, url)
        if programme:
            return programme

        dates = find_dates(text, ref)
        explicit = bool(dates)
        if not dates:
            if RECAP.search(text):
                return []
            d = relative_date(text, ref)
            if not d:
                return []
            dates = [(d, 0, False)]
        dates = [x for x in dates if x[0] >= ref - timedelta(days=1)]
        if not dates:
            return []
        if len(dates) > 6:
            dates = dates[:1]

        tn = norm(text)
        handles = self.handles(text)
        venue = acc_venue
        if not venue:
            venue = next((v for h in handles if (v := self.city.venue_by_handle(h))), None) or self.city.venue_in_text(tn)
        venue_guess = False
        if not venue and acc_prom and len(acc_prom.get("venues") or []) == 1:
            venue, venue_guess = self.city.venue_named(acc_prom["venues"][0]), True
        raw_place = None
        if not venue:
            m = re.search(r"📍\s*([^\n]{3,60})", text) or re.search(r"(?:^|\s)@\s?([A-Z][^\n,.|]{2,40})", text)
            raw_place = clean_name(m.group(1)) if m else None
        addr = ADDRESS.search(text)

        times = find_times(text)
        entry, price = find_price(text)
        typ = find_type(text)
        genre = find_genre(text)
        tickets = next((u.rstrip(").,") for u in URL.findall(text) if re.search(r"xceed|ticket|upass|eventbrite|dice\.fm|shotgun|fourvenues|/bk/|/fk/|prenot|book", u, re.I)), None)

        sections = self._rooms(text)
        events = []
        for d, _, ex in dates:
            for si, (room, chunk) in enumerate(sections):
                proms = [p for h in self.handles(chunk) if (p := self.city.promoter(h)) and p is not acc_prom]
                prom = (acc_prom if (si == 0 or not proms) and acc_prom else None) or (proms[0] if proms else None)
                if not prom and len(sections) == 1:
                    prom = self.city.promoter_in_text(tn)
                skip = {norm(x) for x in [venue["name"] if venue else "", *(venue.get("aliases") or [] if venue else []), prom["name"] if prom else "", account or ""] if x}
                guests = self.guests(chunk if len(sections) > 1 else text, skip)
                party = (prom.get("party_name") or prom["name"]) if prom else (self.title_line(text) if len(sections) == 1 or room in (None, "main room") else None)
                if party and not prom:
                    cut = norm(party)
                    for a in sorted([venue["name"], *(venue.get("aliases") or [])] if venue else [], key=len, reverse=True):
                        cut = re.sub(rf"\b{re.escape(norm(a))}\b", " ", cut)
                    cut = ROOM.sub(" ", cut).strip()
                    cut = re.sub(r"\s+(?:x|at|presenta|presents)$", "", cut).strip()
                    party = None if len(cut) < 3 else (party if cut == norm(party) else cut.title())
                conf = 0.45 + (0.2 if ex else 0) + (0.1 if times["start"] else 0) + (0.15 if venue and not venue_guess else 0)
                conf += 0.1 if (typ == "dj set" or prom or guests or (venue or {}).get("type") == "club") else 0
                conf -= 0.15 * (len(dates) > 1) + 0.1 * (venue is None)
                events.append({
                    "date": d.isoformat(), "start": times["start"], "end": times["end"], "late_end": times["late_end"],
                    "venue": venue["name"] if venue else raw_place, "venue_known": bool(venue), "address": addr.group(0).strip() if addr and not venue else None,
                    "room": room, "party": party, "promoter": prom["instagram"] if prom and prom.get("instagram") else None,
                    "promoter_name": prom["name"] if prom else None, "guests": guests, "genre": find_genre(chunk) if len(sections) > 1 and find_genre(chunk) else genre,
                    "type": typ, "entry": entry, "price": price, "tickets": tickets, "handles": handles,
                    "confidence": round(max(0.1, min(conf, 0.95)), 2), "source": source, "account": account, "url": url,
                    "posted": when.isoformat(timespec="minutes"), "text": text[:600],
                })
        return events

    def _rooms(self, text: str) -> list[tuple[str | None, str]]:
        """Divide il testo per sala quando ne nomina più di una: ogni sala è una serata a sé."""
        marks = [(m.start(), re.sub(r"\s+", " ", m.group(1).lower())) for m in ROOM.finditer(text)]
        rooms = []
        for pos, r in marks:
            r = {"main area": "main room", "main stage": "main room", "theatre room": "theater room"}.get(r, r)
            if r not in [x[1] for x in rooms]:
                rooms.append((pos, r))
        if len(rooms) < 2:
            only = rooms[0][1] if len(rooms) == 1 and re.match(r"club room|lab room|theater room|second room|sala (?:2|due)|elysium|el cielo|bunker|room 2", rooms[0][1]) else None
            return [(only, text)]
        out = []
        for i, (pos, r) in enumerate(rooms):
            end = rooms[i + 1][0] if i + 1 < len(rooms) else len(text)
            chunk = text[pos:end]
            if i + 1 == len(rooms):  # l'ultima sala finisce alla prima riga vuota
                chunk = re.split(r"\n\s*\n", chunk)[0]
            out.append((r, chunk))
        return out

    def _programme(self, text, ref, acc_venue, acc_prom, account, source, url) -> list[dict]:
        """Programma del mese: una riga per sera ("Sat 21st premiere + the.circle")."""
        rows, every = [], []
        for line in text.splitlines():
            l = line.strip()
            m = EVERY.search(l)
            if m and m.group(2).strip():
                every.append((WEEKDAYS[m.group(1).lower()], m.group(2).strip()))
                continue
            m = PROGRAMME_LINE.match(l)
            if m and 1 <= int(m.group(1)) <= 31 and len(m.group(3)) < 80 and not re.search(r"\d{1,2}[:.]\d{2}", m.group(3)):
                rows.append((int(m.group(1)), int(m.group(2)) if m.group(2) else None, m.group(3).strip()))
        if len(rows) < 3 or not acc_venue:
            return []
        month_ref = ref if ref.day <= 20 else (ref.replace(day=1) + timedelta(days=32)).replace(day=1)
        out = []
        def emit(d: date, what: str, conf: float):
            names = [n for n in re.split(r"\s*(?:\+|,|&|/)\s*", what) if n.strip()]
            # "cromaroom + Bar Florio": un nome senza profilo e non conosciuto è un ospite della serata, non un'altra sala
            names = names[:1] + [n for n in names[1:] if self.handles(n) or self.city.promoter_in_text(norm(n))]
            for i, n in enumerate(names):
                hs = self.handles(n)
                prom = self.city.promoter(hs[0]) if hs else self.city.promoter_in_text(norm(n))
                out.append({
                    "date": d.isoformat(), "start": None, "end": None, "late_end": False, "venue": acc_venue["name"], "venue_known": True, "address": None,
                    "room": None if len(names) == 1 else ("main room" if i == 0 else "club room"),
                    "party": (prom.get("party_name") or prom["name"]) if prom else clean_name(n).title(), "promoter": prom.get("instagram") if prom else (hs[0] if hs else None),
                    "promoter_name": prom["name"] if prom else None, "guests": [], "genre": None, "type": "dj set", "entry": None, "price": None, "tickets": None,
                    "handles": hs, "confidence": conf, "source": source, "account": account, "url": url, "posted": ref.isoformat(), "text": f"{what} (programma del mese)", "programme": True,
                })
        for day, month, what in rows:
            try:   # "giovedì 29/10 > space jam": il mese scritto vince (un riepilogo di ottobre non diventa novembre)
                d = date(month_ref.year, month or month_ref.month, day)
                if month and d < ref - timedelta(days=200):
                    d = d.replace(year=d.year + 1)
            except ValueError:
                continue
            if d >= ref - timedelta(days=1):
                emit(d, what, 0.7)
        for wd, what in every:
            d = month_ref.replace(day=1)
            while d.month == month_ref.month:
                if d.weekday() == wd and d >= ref:
                    emit(d, what, 0.65)
                d += timedelta(days=1)
        return out
