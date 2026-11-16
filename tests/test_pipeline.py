from datetime import datetime
from zoneinfo import ZoneInfo

from bget.calendar_ics import build_ics
from bget.config import Config
from bget.models import Artist, Category, Event, Scope, Venue
from bget.relevance import score_event
from bget.sources.jsonld import JsonLdSource
from bget.store import Store

TZ = ZoneInfo("Europe/Rome")
cfg = Config.load()


def ev(title, venue, city, genres=(), cat=Category.CLUB, artists=(), hour=23):
    v = cfg.enrich_venue(Venue(name=venue, city=city))
    return Event(title=title, start=datetime(2026, 11, 20, hour, tzinfo=TZ), venue=v,
                 genres=list(genres), category=cat, artists=[Artist(a) for a in artists])


def test_local_techno_is_kept():
    e = score_event(ev("Night", "Bolgia", "Osio Sopra", ["techno"]), cfg)
    assert e.scope == Scope.LOCAL and e.distance_km < 20


def test_plain_concert_is_dropped():
    e = score_event(ev("Live", "Druso", "Ranica", ["rock"], Category.CONCERT), cfg)
    assert e.scope == Scope.IGNORE


def test_crossover_concert_is_kept():
    e = score_event(ev("Bicep live", "Druso", "Ranica", ["electronic"], Category.CONCERT, ["Bicep"]), cfg)
    assert e.scope == Scope.LOCAL and e.alert_level == "regional"


def test_milan_headliner_is_alert_but_milan_small_night_is_not():
    big = score_event(ev("X", "Magazzini Generali", "Milano", ["techno"], artists=["Amelie Lens"]), cfg)
    small = score_event(ev("Y", "Magazzini Generali", "Milano", ["techno"]), cfg)
    assert big.scope == Scope.ALERT and small.scope == Scope.IGNORE


def test_after_midnight_merges_with_same_night():
    s = Store(cfg.root / "data" / "_test_events.json")
    a = ev("Night", "Bolgia", "Osio Sopra", ["techno"], artists=["A"])
    b = ev("Night other source", "Bolgia", "Osio Sopra", artists=["B"], hour=23)
    b.start = datetime(2026, 11, 21, 1, tzinfo=TZ)
    assert s.upsert(a) == "new" and s.upsert(b) == "updated"
    assert [x.name for x in s.all()[0].artists] == ["A", "B"]


def test_jsonld_extracts_handles():
    src = JsonLdSource({"id": "t", "urls": []}, cfg)
    html = (cfg.root / "tests/fixtures/sample_event_page.html").read_text()
    events = list(src.parse(html, "file://x"))
    assert events[0].artists[0].socials.instagram == "sample.house.dj"
    assert events[0].artists[0].socials.tiktok == "samplehousedj"
    assert events[0].venue.socials.instagram == "sample_venue"


def test_ics_is_valid_shape():
    text = build_ics([score_event(ev("Night, with comma", "Bolgia", "Osio Sopra", ["techno"]), cfg)], "T")
    assert text.startswith("BEGIN:VCALENDAR") and "SUMMARY:Night\\, with comma" in text
    assert all(len(l.encode()) <= 75 for l in text.split("\r\n"))
