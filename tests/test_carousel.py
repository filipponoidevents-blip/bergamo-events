from datetime import date, datetime
from zoneinfo import ZoneInfo

from bget import carousel
from bget.cli import due_posts
from bget.models import Category, Event, Venue

TZ = ZoneInfo("Europe/Rome")


def ev(day, hour=23, venue="Bolgia", title="Warehouse"):
    return Event(title=title, start=datetime(2026, 11, day, hour, 30, tzinfo=TZ),
                 venue=Venue(name=venue, city="Osio Sopra"), category=Category.CLUB, genre="Techno")


def test_weekly_cover_plus_one_slide_per_date_in_both_formats():
    evs = [ev(20), ev(21), ev(21, venue="Zero Club"), ev(23)]   # 23 is next week
    for fmt, size in [("ig", (1080, 1350)), ("tt", (1080, 1920))]:
        slides = carousel.weekly(evs, date(2026, 11, 16), fmt=fmt)
        assert len(slides) == 3 and all(img.size == size for img, _ in slides)
        assert [len(e) for _, e in slides] == [0, 1, 2]


def test_after_midnight_start_stays_on_its_night():
    slides = carousel.weekly([ev(22, hour=0)], date(2026, 11, 16))   # Sunday 00:30 = Saturday night
    assert slides[1][1][0].night == date(2026, 11, 21)


def test_month_stays_within_instagram_limit():
    evs = [ev(d, venue=v) for d in range(1, 31) for v in ("Bolgia", "Zero Club")]   # 60 nights, a very busy month
    slides = carousel.monthly(evs, 2026, 11)
    assert len(slides) <= carousel.MAX_SLIDES
    assert sum(len(e) for _, e in slides) == len(evs)


def test_midweek_takes_only_preserata_and_non_music_thursday_to_sunday():
    others = [("food_drink", ev(19, 19)), ("comedy", ev(20, 21)), ("ballo_da_sala", ev(20)),
              ("quiz_games", ev(17, 21))]
    slides = carousel.midweek(others, date(2026, 11, 16))
    shown = [k for _, evs in slides for k in evs]
    assert len(shown) == 2


def test_fancy_instagram_letters_are_folded():
    assert carousel.clean("𝐁𝐎𝐋𝐆𝐈𝐀 🔥 night") == "BOLGIA night"


def test_posting_cadence():
    assert due_posts(date(2026, 11, 16)) == [("week", "2026-11-16")]          # Monday
    assert due_posts(date(2026, 11, 18)) == [("midweek", "2026-11-18")]       # Wednesday
    assert due_posts(date(2026, 11, 29)) == [("month", "2026-12")]            # last Sunday
    assert due_posts(date(2026, 11, 22)) == []                                # not the last Sunday


def test_early_start_is_preserata_not_serata():
    assert carousel.is_night(ev(20, 22)) and carousel.is_night(ev(21, 0))
    assert not carousel.is_night(ev(20, 18)) and not carousel.is_night(ev(20, 21))
    assert carousel.is_night(ev(20, 21), "21:00")


def test_serata_threshold_depends_on_venue_kind():
    from bget.config import Config
    cfg = Config.load()
    club, bar = Venue("Esempio Club", "Bergamo", kind="club"), Venue("Esempio Bar", "Bergamo", kind="bar")
    assert cfg.serata_from(club) == "00:00" and cfg.serata_from(bar) == "23:00"
    e = ev(20, 21)
    assert carousel.is_night(e, cfg.serata_from(club)) and not carousel.is_night(e, cfg.serata_from(bar))
