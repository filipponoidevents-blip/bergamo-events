from datetime import datetime, timezone

from bget.config import Config
from bget.sources.instagram_rules import RulesExtractor, find_dates, find_times

cfg = Config.load()
rx = RulesExtractor(cfg)
POSTED = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)  # a Monday


def read(caption, account="someclub", venue=None):
    return rx(caption=caption, image_urls=[], posted_at=POSTED, account=account, account_venue=venue)


def test_italian_flyer_caption():
    ex = read("TECHNO SOCIETY 🖤\nSabato 21 novembre\nh 23:30 – 05:00\nLine up: @dj.one b2b @dj_two\n"
              "Tickets 👉 https://dice.fm/event/abc\n#techno #bergamo", venue="Bolgia")
    assert ex.is_event_announcement and ex.confidence >= 0.85
    e = ex.events[0]
    assert (e.title, e.date, e.start_time, e.end_time) == ("TECHNO SOCIETY", "2026-11-21", "23:30", "05:00")
    assert [a.instagram for a in e.artists] == ["dj.one", "dj_two"]
    assert e.ticket_url.startswith("https://dice.fm") and e.venue_name == "Bolgia" and "techno" in e.genres


def test_numeric_date_and_named_venue_in_caption():
    ex = read("Venerdì 27.11 tech house all night @ Edonè, dalle 23 alle 4")
    e = ex.events[0]
    assert e.date == "2026-11-27" and e.venue_name == "Edoné" and e.start_time == "23:00" and e.end_time == "04:00"
    assert "tech house" in e.genres


def test_weekday_only_is_lower_confidence():
    ex = read("This saturday house music vibes 🔊")
    assert ex.events[0].date == "2026-11-21" and ex.confidence < 0.85


def test_recap_without_future_date_is_not_an_event():
    assert not read("Grazie a tutti per ieri sera! What a night 🔥").is_event_announcement


def test_past_dates_ignored_and_year_rollover():
    assert find_dates("10 novembre", POSTED.date())[0] == []
    assert find_dates("3 gennaio", POSTED.date())[0][0].isoformat() == "2027-01-03"


def test_times():
    assert find_times("doors 22:00") == ("22:00", None)
    assert find_times("apertura ore 10 colazione") == ("10:00", None)  # daytime: kept, so it can be told apart


# Filippo's rules: weekday alone -> same week; day number alone -> same month as the post.
def test_weekday_alone_is_same_week():
    thu = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
    assert find_dates("venerdì techno", thu.date())[0][0].isoformat() == "2026-11-20"
    assert find_dates("domenica pomeriggio", thu.date())[0][0].isoformat() == "2026-11-22"


def test_weekday_already_passed_rolls_to_next_week_with_warning():
    w = []
    sat = datetime(2026, 11, 21, 12, tzinfo=timezone.utc).date()
    assert find_dates("venerdì si balla", sat, w)[0][0].isoformat() == "2026-11-27" and w


def test_day_number_alone_is_same_month():
    assert find_dates("sabato 21", POSTED.date())[0][0].isoformat() == "2026-11-21"
    assert find_dates("ci vediamo il 28", POSTED.date())[0][0].isoformat() == "2026-11-28"
    # already past this month -> next month
    late = datetime(2026, 11, 28, tzinfo=timezone.utc).date()
    assert find_dates("sabato 5", late)[0][0].isoformat() == "2026-12-05"


def test_weekday_number_mismatch_lowers_confidence():
    good = read("Sabato 21 techno h 23:30", venue="Bolgia")
    bad = read("Sabato 22 techno h 23:30", venue="Bolgia")
    assert bad.confidence < good.confidence and "not that weekday" in bad.notes


import pytest

from bget.sources import ocr
from bget.sources.instagram_rules import ListingExtractor

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def test_listing_text_splits_by_day_and_venue():
    text = "AGENDA DELLA SETTIMANA\nVENERDÌ 20\nBOLGIA - Techno Society\nSABATO 21\nEdonè\nHouse Session\nDRUSO - Indie Night"
    ex = ListingExtractor(cfg)(caption=text, image_urls=[], posted_at=POSTED, account="agg", account_venue=None)
    got = [(e.date, e.venue_name, e.title) for e in ex.events]
    assert got == [("2026-11-20", "Bolgia", "Techno Society"), ("2026-11-21", "Edoné", "House Session"),
                   ("2026-11-21", "Druso", "Indie Night")]
    assert ex.confidence < 0.85  # always reviewed


@pytest.mark.skipif(not ocr.available(), reason="tesseract not installed")
def test_ocr_reads_agenda_image():
    text = ocr.ocr_bytes((FIXTURES / "agenda.png").read_bytes())
    ex = ListingExtractor(cfg)(caption=text, image_urls=[], posted_at=POSTED, account="agg", account_venue=None)
    assert [(e.date, e.venue_name) for e in ex.events] == [
        ("2026-11-20", "Bolgia"), ("2026-11-21", "Edoné"), ("2026-11-21", "Druso")]


def test_title_skips_a_leading_date_line():
    ex = read("VENERDÌ 20 NOVEMBRE\nDEEP INSIDE w/ Sample DJ\nh 23:00", venue="Bolgia")
    assert ex.events[0].title == "DEEP INSIDE"  # the headliner is added back by the title convention


from bget.sources.categories import categorize


def test_categories():
    assert categorize("Sabato techno all night w/ guest DJ") == "dj_club"
    assert categorize("Aperitivo con dj set") == "dj_club"
    assert categorize("Torna la comicità dal vivo, stand up comedy") == "comedy"
    assert categorize("Mega tornei al pub e quiz") == "quiz_games"
    assert categorize("Presentazione del libro") == "culture"
    assert categorize("Brunch della domenica, menu speciale") == "food_drink"
    assert categorize("Band live in concerto") == "live_music"
    assert categorize("VENERDÌ 20 NOVEMBRE", venue_kind="club") == "dj_club"
    assert categorize("Something happening") == "other"


def test_genre_detect_order():
    from bget.genres import detect
    assert detect("Hard techno all night") == "Hard Techno"
    assert detect("tech-house party") == "Tech House"
    assert detect("Techno night") == "Techno"
    assert detect("aperitivo") is None


def test_past_full_date_is_not_rolled_to_next_month():
    from datetime import date
    from bget.sources.instagram_rules import find_dates
    posted = date(2026, 11, 16)
    assert find_dates("Alcuni scatti di mercoledì 04/11", posted)[0] == []
    assert find_dates("il live di Sabato 24 Ottobre!", posted)[0] == []
    assert find_dates("Sabato 21 Novembre dalle 23:30", posted)[0] == [date(2026, 11, 21)]


def test_genre_overrides():
    from datetime import datetime
    from bget.config import Config
    from bget.genres import GenreAssigner
    from bget.models import Event, Venue
    cfg = Config.load()
    cfg.genre_overrides = {"nights": [{"venue": "Bolgia", "date": "2026-11-21", "genre": "Italo disco"}],
                           "venues": {"Bolgia": "Hard techno"}}
    g = GenreAssigner(cfg)
    ev = lambda title, start: Event(title=title, start=start, venue=Venue(name="Bolgia", city="Bergamo"))
    assert g.assign(ev("Sabato", datetime(2026, 11, 22, 1, 0))) == ("Italo Disco", "manual")
    assert g.assign(ev("Sabato", datetime(2026, 11, 28, 23, 0))) == ("Hard Techno", "venue")
    assert g.assign(ev("House night", datetime(2026, 11, 28, 23, 0))) == ("House", "event")


def test_caption_genres():
    from bget.genres import detect, detect_all
    from bget.sources.instagram_rules import find_genres
    assert find_genres("Discoteca Queen sabato sera", ["disco", "house"]) == []
    assert detect("#discoteca #bergamo reggaeton e commerciale") == "Latin / Reggaeton"
    assert detect("#technobergamo") == "Techno"
    assert detect("musica HOUSE e hits") == "House"
    assert "Commerciale" in detect_all("house e commerciale")
    assert detect("pop-up store") is None
    assert "techno" in find_genres("Techno all night", ["techno", "house"])


def test_calendar_start_only_and_midnight_on_the_evening():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from bget.calendar_ics import build_ics, calendar_start
    from bget.models import Event, Venue
    rome = ZoneInfo("Europe/Rome")
    ev = Event(title="Sabato", start=datetime(2026, 11, 22, 0, 0, tzinfo=rome), venue=Venue(name="Bolgia", city="Dalmine"))
    assert calendar_start(ev) == datetime(2026, 11, 21, 23, 59, tzinfo=rome)
    ics = build_ics([ev], "t")
    assert "DTSTART:20261121T225900Z" in ics and "DTEND" not in ics.split("BEGIN:VEVENT")[1]


def test_title_convention():
    from datetime import datetime
    from bget.calendar_ics import night_summary
    from bget.models import Artist, Event, Socials, Venue
    v = Venue(name="Bolgia", city="Osio Sopra", socials=Socials(instagram="bolgia.official"))
    a = [Artist(name="bolgia.official", socials=Socials(instagram="bolgia.official")), Artist(name="Ilario Alicante")]
    from bget.config import Config
    from bget.headliners import HeadlinerFinder
    ev = Event(title="Sabato 21 Novembre dalle ore 23:30 alle 06:00!", start=datetime(2026, 11, 21, 23, 30), venue=v,
               artists=a, genre="Techno", description="Main room con ILARIO ALICANTE")
    ev.headliners = HeadlinerFinder(Config.load()).find(ev)
    assert ev.headliners == ["Ilario Alicante"]
    assert night_summary(ev) == "[Techno] Bolgia w/ Ilario Alicante"
    ev.title = "Oasi"
    assert night_summary(ev) == "[Techno] Bolgia · Oasi w/ Ilario Alicante"
    ev.title = "Sabato 21 Novembre"
    ev.headliners = []
    assert night_summary(ev) == "[Techno] Bolgia"
    ev.title = "Oasi"
    assert night_summary(ev) == "[Techno] Bolgia · Oasi"


def test_lineup_lines():
    from bget.headliners import lineup_names
    assert lineup_names("In consolle:\nDj : StefanOne + Andrew V.\nVoice : Matteo") == ["StefanOne", "Andrew V"]
    assert lineup_names("LINE UP: Nesta b2b Lapo Underground") == ["Nesta", "Lapo Underground"]


def test_no_dj_for_commercial_nights():
    from datetime import datetime
    from bget.config import Config
    from bget.headliners import HeadlinerFinder
    from bget.models import Event, Venue
    ev = Event(title="Notte a caso", start=datetime(2026, 11, 21, 23), venue=Venue(name="Queen Club", city="Seriate"),
               description="Dj: StefanOne + Andrew V.", genre="Revival")
    assert HeadlinerFinder(Config.load()).find(ev) == []
    ev.genre = "Techno"
    assert HeadlinerFinder(Config.load()).find(ev) == ["StefanOne", "Andrew V"]


def test_press_article_reader():
    from datetime import datetime, timezone
    from bs4 import BeautifulSoup
    from bget.config import Config
    from bget.sources.press import PressSource, article_parts
    html = """<html><head><script type="application/ld+json">{"@type":"NewsArticle","headline":"Halloween a Daste,
    Le Cannibale torna a far ballare","datePublished":"2026-11-10T10:00:00+01:00",
    "articleBody":"Sabato 21 novembre dalle 23:00 al Daste torna il clubbing: dj set house e techno fino all'alba."}
    </script></head><body></body></html>"""
    title, body, pub = article_parts(BeautifulSoup(html, "html.parser"))
    assert title.startswith("Halloween a Daste") and pub.year == 2026
    src = PressSource({"id": "t", "max_age_days": 10000}, Config.load())
    src.fetch = lambda url: html
    from bget.sources.instagram_rules import RulesExtractor
    from zoneinfo import ZoneInfo
    evs = list(src._article("https://x/a", RulesExtractor(Config.load()), ZoneInfo("Europe/Rome")))
    assert len(evs) == 1 and evs[0].venue.name.startswith("Daste") and evs[0].start.hour == 23


# ---- data collection improvements (2026-11-17) ----------------------------------------------------
from bget.sources.instagram_rules import entry_hint, fascia_hint, party_title, read_times
from bget.text import normalize


def test_normalize_fancy_letters():
    assert normalize("𝐏𝐎𝐏 𝐔𝐏 𝐏𝐀𝐑𝐓𝐘") == "POP UP PARTY"
    assert normalize("𝑷𝒓𝒐𝒔𝒔𝒊𝒎𝒊 𝒆𝒗𝒆𝒏𝒕𝒊") == "Prossimi eventi"
    assert normalize("ᴄᴄ ɴɪɢʜᴛ") == "CC NIGHT"
    assert normalize("𝟐𝟎 𝐧𝐨𝐯𝐞𝐦𝐛𝐫𝐞") == "20 novembre"


@pytest.mark.parametrize("text,start,end,late", [
    ("Sabato 21 Novembre dalle ore 23:30 alle 06:00!", "23:30", "06:00", False),
    ("> Opening ore 22:30 // Chiusura ore 04:00", "22:30", "04:00", False),
    ("🕚 h. 23 – 03", "23:00", "03:00", False),
    ("da mezzanotte alle 04:00", "00:00", "04:00", False),
    ("⏰ Dalle 11.30 pm alle 4 am", "23:30", "04:00", False),
    ("🕖 ** 19:00 23:00**", "19:00", "23:00", False),
    ("— 00.00 till very late", "00:00", None, True),
    ("⏰ 21:00-2:00", "21:00", "02:00", False),
    ("APERTURA ORE 21,30", "21:30", None, False),
    ("20.11 MAIN ROOM", None, None, False),                       # a date, not 20:11
    ("Ingresso OMAGGIO DONNA fino alle 23:00", None, None, False),  # entry condition
    ("🎫 Prezzo 12€ con drink entro l'1.00", None, None, False),
    ("Dale 23:30 alle 04:30\nDalle 02:00 uscita libera", "23:30", "04:30", False),
    ("🍽 Dalle 20:00 — Cena\n🎤 Dalle 23:00 alle 01:30 — LIVE\n🎧 Dalle 01:30 — DJ SET TILL LATE", "20:00", None, True),
    ("20:00 - 22:00 a\n22:00 - 00:00 b", "20:00", "00:00", False),
    ("19 novembre 2026, dalle 9.30 alle 17.30", "09:30", "17:30", False),
    ("𝐝𝐚𝐥𝐥𝐞 𝟐𝟑:𝟑𝟎", "23:30", None, False),
])
def test_read_times(text, start, end, late):
    t = read_times(text)
    assert (t.start, t.end, t.late) == (start, end, late)


@pytest.mark.parametrize("caption,name", [
    ("Sabato 28 Novembre dalle ore 23:30 alle 06:00!\n💰Lista 20€ Dr*nk Incluso💰\nSpecial Guest: MARIE VAUNT", ""),
    ("VENERDÌ 20.11 | dalle 21:00 con cheri.it 🍒\n\nVia Trieste 38, Chiuduno (Bg)", ""),
    ("21 NOVEMBRE [ENTER THE VOID]\n\nMAIN AREA - enter the void\n\nTHE BLOCK w/ palmas.night", "ENTER THE VOID"),
    ("VENERDÌ SI TORNA IN CENTRO BG. 🎓\n\nUNIBG PARTY w/ L'OPERÀ\n📍 ZERO CLUB", "UNIBG PARTY"),
    ("𝐏𝐎𝐏 𝐔𝐏 𝐏𝐀𝐑𝐓𝐘\n\n📅 VENERDÌ 20 NOVEMBRE", "POP UP PARTY"),
    ("Ci vediamo il 21 nel club 🪤\n\nLineup:\nfssquah\nluca._stanzione", ""),
    ("THIS WEEK.\nONE NIGHT.\nNO EXCUSES.", ""),
    ("THIS IS DNB \nBergamo Edition\n20:30 - ...", "THIS IS DNB"),
    ("OASI returns.\n\nFRI. 20 NOV.\n23:30 — 04:30", "OASI"),
    ("ཐ ᴄᴄ ɴɪɢʜᴛ ཋ\n♱ ᴅᴏᴏʀꜱ ᴏᴘᴇɴ ᴀᴛ 00:00", "CC NIGHT"),
])
def test_party_title(caption, name):
    assert party_title(caption) == name


def test_entry_and_fascia_hints():
    assert entry_hint("💰Lista 20€ Dr*nk Incluso💰") == "paid"
    assert entry_hint("🎟️ INGRESSO LIBERO") == "free"
    assert entry_hint("INGRESSO GRATUITO A TUTTE LE DONNE ENTRO LE ORE 22.00") is None
    assert fascia_hint("Aperitivo con dj set") == "preserata"
    assert fascia_hint("after hour fino all'alba") == "serata"


def test_daytime_conference_is_not_a_club_night():
    ex = read("Il 19 novembre torna Cities For Impact, urban talk\n19 novembre 2026, dalle 9.30 alle 17.30",
              venue="Gres Art 671")
    assert ex.events[0].kind != "dj_club"


def _ev(start, end=None, **kw):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from bget.models import Event, Venue
    rome = ZoneInfo("Europe/Rome")
    s = datetime.fromisoformat(f"2026-11-21T{start}").replace(tzinfo=rome)
    if s.hour < 7:
        s = s.replace(day=22)
    e = datetime.fromisoformat(f"2026-11-22T{end}").replace(tzinfo=rome) if end else None
    if e and e.hour >= 7:
        e = e.replace(day=21)
    return Event(title="x", start=s, end=e, venue=kw.pop("venue", Venue(name="Somewhere", city="")), **kw)


def test_fascia_rule():
    from bget.fascia import classify
    from bget.models import Venue
    assert classify(_ev("23:30", "06:00"), cfg)[0] == "serata"
    assert classify(_ev("21:00", "02:00"), cfg)[0] == "preserata"      # ends before 03:00
    assert classify(_ev("00:00"), cfg)[0] == "serata"                  # starts after midnight
    assert classify(_ev("21:00"), cfg)[0] == "preserata"               # early start, no end
    assert classify(_ev("21:00", late_end=True), cfg)[0] == "serata"   # "till late"
    assert classify(_ev("22:30", entry="paid"), cfg)[0] == "serata"
    assert classify(_ev("22:30", entry="free"), cfg)[0] == "preserata"
    assert classify(_ev("23:00", start_known=False, venue=Venue(name="Bolgia", city="")), cfg)[0] == "serata"
    assert classify(_ev("23:00", start_known=False, venue=Venue(name="MINT", city="")), cfg)[0] == "preserata"
    assert classify(_ev("23:30", "06:00", fascia="preserata"), cfg) == ("preserata", "deciso a mano")


def test_known_promoter_names_the_night():
    ex = read("VENERDÌ 20.11 | dalle 21:00 con cheri.it 🍒\n\nVia Trieste 38, Chiuduno (Bg)", account="mint_lounge",
              venue="MINT")
    assert ex.events[0].title == "Cheri"
    assert all(a.instagram != "cheri.it" for a in ex.events[0].artists)


def test_venue_post_named_after_tagged_promoter():
    ex = read("THIS WEEK.\nONE NIGHT.\nNO EXCUSES.\n\n20.11 MAIN ROOM:oasi.night \nTHEATER ROOM: upside.down.night \n"
              "BE COOL. BE VIBE. 🔥", account="vibetheclub", venue="Vibe Club")
    assert ex.events[0].title == "OASI"
    ex = read("OASI returns.\n\nFRI. 20 NOV.\n23:30 — 04:30\n\nvibetheclub", account="oasi.night")
    assert ex.events[0].venue_name == "Vibe Club"


def test_posting_promoter_names_the_night():
    ex = read("𝐏𝐎𝐏 𝐔𝐏 𝐏𝐀𝐑𝐓𝐘\n\n📅 VENERDÌ 20 NOVEMBRE\n📍 tassinocafe\n🕖 ** 19:00 23:00**", account="theboxbergamo")
    assert ex.events[0].title == "The Box"


def test_time_after_month_is_not_a_day():
    from bget.sources.instagram_rules import find_dates
    from datetime import date
    assert find_dates("14 novembre \n21:00-00:00", date(2026, 11, 8))[0] == [date(2026, 11, 14)]


def test_handles_shown_as_names():
    from bget.names import display, known_names
    names = known_names(cfg)
    assert display("@bolgia.official", names) == "Bolgia"
    assert display("theboxbergamo", names) == "The Box"
    assert display("someone.unknown", names) == "someone.unknown"


def test_daytime_talk_is_culture():
    ex = read("Il 19 novembre torna Cities For Impact, urban talk. Intervengono A e B\n19 novembre 2026, dalle 9.30 alle 17.30",
              venue="Gres Art 671")
    assert ex.events[0].kind == "culture"


def test_rank_order():
    from datetime import datetime
    from bget.models import Event, Venue
    from bget.ranking import Ranker
    r = Ranker(cfg)
    bolgia = Event(title="Bolgia", start=datetime(2026, 11, 21, 23), venue=Venue(name="Bolgia", city=""))
    bar = Event(title="x", start=datetime(2026, 11, 21, 21), venue=Venue(name="Unplug Bar", city=""))
    room = Event(title="Zero Club - Club room", start=datetime(2026, 11, 21, 23), venue=Venue(name="Zero Club", city=""))
    main = Event(title="Zero Club - Main room", start=datetime(2026, 11, 21, 23), venue=Venue(name="Zero Club", city=""))
    assert r.rank(bolgia) > r.rank(main) > r.rank(room) > r.rank(bar)
    guest = Event(title="Bolgia", start=datetime(2026, 11, 21, 23), venue=Venue(name="Bolgia", city=""),
                  description="Special Guest: MARIE VAUNT")
    assert r.rank(guest) == r.rank(bolgia)  # untagged guest = probably not relevant


def test_party_score_from_followers():
    from datetime import datetime
    from bget.models import Event, Venue
    from bget.ranking import Ranker, follower_score
    assert follower_score(None) == 0 and follower_score(1000) == 2 and follower_score(100000) == 10
    r = Ranker(cfg)
    r.followers = {"bigparty": 20000, "smallparty": 800}
    ev = lambda h: Event(title="x", start=datetime(2026, 11, 21, 23), venue=Venue(name="Zero Club", city=""),
                         description=f"with @{h}")
    r.by_followers = [("bigparty", follower_score(20000)), ("smallparty", follower_score(800))]
    assert r.rank(ev("bigparty")) > r.rank(ev("smallparty"))


def test_hidden_events(tmp_path):
    from datetime import datetime
    from bget.models import Event, Venue
    from bget.store import Store
    st = Store(tmp_path / "e.json")
    st.upsert(Event(title="All that Swing", start=datetime(2026, 11, 22, 20), venue=Venue(name="NXT Station", city=""),
                    description="rassegna Bergamo on Ice & Star Garden"))
    st.upsert(Event(title="Bolgia", start=datetime(2026, 11, 22, 23), venue=Venue(name="Bolgia", city="")))
    assert st.hide([{"text": "bergamo on ice"}, {}]) == 1
    assert [e.title for e in st.all()] == ["Bolgia"]


def test_venue_name_is_not_a_room():
    from datetime import datetime
    from bget.models import Event, Venue
    from bget.ranking import Ranker
    r = Ranker(cfg)
    ev = Event(title="Zero Club", start=datetime(2026, 11, 21, 23), venue=Venue(name="Zero Club", city=""))
    assert r.factors(ev)["room"] == 10


def test_zero_monthly_programme():
    from datetime import datetime
    from bget.sources.instagram import MONTHLY_HINT
    from bget.sources.instagram_rules import ListingExtractor
    assert MONTHLY_HINT.search("Questo mese a Zero")
    assert not MONTHLY_HINT.search("Sabato 21 novembre ci vediamo")
    text = """NOVEMBRE
VENERDÌ 20/11
UNIBG PARTY w/ L'OPERÀ
dalle 23:00
SABATO 21/11
@premiere.premiere
hip hop / trap
VENERDÌ 27/11
CC NIGHT
00:00 - 05:00"""
    ex = ListingExtractor(cfg)(caption=text, image_urls=[], posted_at=datetime(2026, 11, 2, 18),
                               account="zeroclub.bg", account_venue="Zero Club")
    got = [(e.date, e.title, e.start_time, e.venue_name) for e in ex.events]
    assert got[0][:3] == ("2026-11-20", "UNIBG PARTY", "23:00")
    assert got[1][:2] == ("2026-11-21", "Premiere")
    assert got[2][:3] == ("2026-11-27", "CC NIGHT", "00:00")
    assert all(g[3] == "Zero Club" for g in got)


def test_lineup_counts_only_relevant_names():
    from datetime import datetime
    from bget.models import Artist, Event, Socials, Venue
    from bget.ranking import Ranker
    r = Ranker(cfg)
    r.followers = {"bigdj": 50000, "localdj": 900}
    mk = lambda h: Event(title="x", start=datetime(2026, 11, 21, 23), venue=Venue(name="Queen Club", city=""),
                         description="LINE UP\nDJ ONE\nDJ TWO", artists=[Artist(name=h, socials=Socials(instagram=h))])
    assert r.guest(mk("localdj")) == 0
    assert r.guest(mk("bigdj")) > 8


def test_second_room_is_its_own_night(tmp_path):
    from datetime import datetime
    from bget.models import Event, Venue
    from bget.sources.instagram import second_room
    from bget.store import Store
    assert second_room("Sabato 21 Novembre!\nZero Club - Club room") == "club room"
    assert second_room("Main room: X\nClub room: Y") is None
    st = Store(tmp_path / "e.json")
    v = lambda: Venue(name="Zero Club", city="Bergamo")
    st.upsert(Event(title="Zero Club", start=datetime(2026, 11, 21, 23), venue=v(), sources=["ig:1"]))
    st.upsert(Event(title="Premiere", start=datetime(2026, 11, 21, 23), venue=v(), sources=["manual"], room="main room"))
    st.upsert(Event(title="The Circle", start=datetime(2026, 11, 21, 23), venue=v(), sources=["ig:1"], room="club room"))
    assert sorted(e.title for e in st.all()) == ["Premiere", "The Circle"]


def test_seasonal_capacity():
    from datetime import datetime
    from bget.models import Event, Venue
    from bget.ranking import Ranker
    r = Ranker(cfg)
    mk = lambda m: Event(title="x", start=datetime(2026, m, 20, 23), venue=Venue(name="Edoné", city=""))
    assert r.factors(mk(7))["capacity"] > r.factors(mk(11))["capacity"]


def test_venue_always_preserata():
    from datetime import datetime
    from bget.models import Event, Venue
    from bget import fascia
    ev = Event(title="x", start=datetime(2026, 11, 21, 23, 45), venue=Venue(name="NXT Station", city=""), late_end=True)
    assert fascia.classify(ev, cfg)[0] == "preserata"


def test_zero_programme_english_dates_and_every_wednesday():
    """Zero's November programme: "Fri 20th", "[04 Wed]" and "Every wednesday please.nights"."""
    from datetime import datetime
    from pathlib import Path
    from bget.sources.instagram_rules import ListingExtractor
    text = (Path(__file__).parent / "zero_programme.txt").read_text()
    ex = ListingExtractor(cfg)(caption=text, image_urls=[], posted_at=datetime.fromisoformat("2026-11-02T17:08:40+00:00"),
                               account="zeroclub.bg", account_venue="Zero Club")
    got = {e.date: e.title for e in ex.events}
    assert [d for d, t in got.items() if t == "Please"] == ["2026-11-04", "2026-11-11", "2026-11-18", "2026-11-25"]
    assert got["2026-11-20"] == "UniBG Party" and got["2026-11-21"] == "Premiere"
    assert list(got) == sorted(got)  # the order the review ids are built from


def test_hide_reads_instagram_bold_letters():
    from datetime import datetime
    from bget.models import Event, Venue
    from bget.store import Store
    import tempfile, os
    s = Store(__import__("pathlib").Path(tempfile.mkdtemp()) / "e.json")
    s.events["k"] = Event(title="NXT Station", start=datetime(2026, 11, 20, 23), venue=Venue(name="NXT Station", city=""),
                          description="❄️ 𝐁𝐞𝐫𝐠𝐚𝐦𝐨 𝐨𝐧 𝐈𝐜𝐞 & 𝐒𝐭𝐚𝐫 𝐆𝐚𝐫𝐝𝐞𝐧\n𝐒𝐭𝐚𝐬𝐞𝐫𝐚 𝐥𝐢𝐯𝐞")
    assert s.hide([{"text": "Bergamo on Ice"}]) == 1


def test_elysium_is_capogiros_second_room():
    from bget.sources.instagram import second_room
    assert second_room("Sabato in Elysium Privè: hip hop night") == "elysium"


def test_nxt_headline_date_beats_rink_season_footer():
    from datetime import datetime
    from bget.sources.instagram_rules import RulesExtractor
    cap = ("Swing 🎶 @ NXT Bergamo, Piazzale degli Alpini - 22 novembre\nBergamo Swing Collective porta in pista...\n\n"
           "⏰ Inizio alle 20:00\nINGRESSO GRATUITO\n\n❄️ Bergamo on Ice & Star Garden 🎄 Piazzale degli Alpini 💫\n"
           "Dal 20 novembre al 10 gennaio")
    ex = RulesExtractor(cfg)(caption=cap, image_urls=[], posted_at=datetime(2026, 11, 9, 15),
                             account="nxtbergamo", account_venue="NXT Station")
    assert [(e.date, e.start_time, e.title) for e in ex.events] == [("2026-11-22", "20:00", "Swing")]
