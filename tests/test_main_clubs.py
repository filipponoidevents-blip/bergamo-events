from bget.config import Config
from bget.sources.instagram import InstagramSource


def item(i, acc, venue, date, status="pending"):
    return {"id": i, "status": status, "account": acc, "account_venue": venue,
            "event": {"date": date, "venue_name": venue, "title": "x"}}


def test_main_club_programme_nights_need_no_approval_unless_double():
    # Filippo 2026-11-18: Zero is one of the main clubs, its programme nights go in by themselves
    src = InstagramSource({"id": "ig", "accounts": []}, Config.load())
    q = {"items": {}}
    for it in (item("1-0", "zeroclub.bg", "Zero Club", "2026-11-28"),
               item("1-1", "zeroclub.bg", "Zero Club", "2026-11-27"),
               item("2-0", "crystalcastlesnightt", "Zero Club", "2026-11-27", "auto"),
               item("3-0", "smallbar", "Tassino Café", "2026-11-28")):
        q["items"][it["id"]] = it
    ok = {k for k, it in q["items"].items() if it["status"] == "pending" and src._main_club_own_night(q, it)}
    assert ok == {"1-0"}
