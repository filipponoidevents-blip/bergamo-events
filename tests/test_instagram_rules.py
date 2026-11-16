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
    assert e.date == "2026-11-27" and e.venue_name == "Edonè" and e.start_time == "23:00" and e.end_time == "04:00"
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
    assert find_times("apertura ore 10 colazione") == (None, None)
