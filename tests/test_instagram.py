import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from bget.config import Config
from bget.sources.instagram import InstagramSource, write_review_md
from bget.sources.instagram_extract import ExtractedArtist, ExtractedEvent, Extraction
from bget.store import Store

REPO = Path(__file__).resolve().parents[1]


def make_root(tmp_path):
    shutil.copytree(REPO / "config", tmp_path / "config")
    (tmp_path / "data").mkdir()
    return Config.load(tmp_path)


def recent(days_ago=1):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


class FakeGraph:
    def __init__(self, posts):
        self.posts, self.calls = posts, 0

    def recent_media(self, username, limit=12):
        self.calls += 1
        return self.posts.get(username, [])


def future(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


def fake_extractor(*, caption, image_urls, posted_at, account, account_venue):
    if "recap" in caption:
        return Extraction(is_event_announcement=False, confidence=0.9)
    conf = 0.95 if "sure" in caption else 0.5
    return Extraction(is_event_announcement=True, confidence=conf, events=[ExtractedEvent(
        title=caption.split("|")[0].strip(), date=future(5), start_time="23:30", end_time="05:00",
        venue_name=None, city=None, genres=["techno"],
        artists=[ExtractedArtist(name="Sample DJ", instagram="sample.dj")])])


POSTS = {"club_a": [
    {"id": "1", "caption": "Techno night | sure", "media_type": "IMAGE", "media_url": "https://x/1.jpg",
     "permalink": "https://instagram.com/p/1", "timestamp": recent()},
    {"id": "2", "caption": "Maybe party | guess", "media_type": "IMAGE", "media_url": "https://x/2.jpg",
     "permalink": "https://instagram.com/p/2", "timestamp": recent()},
    {"id": "3", "caption": "recap of last night", "media_type": "IMAGE", "media_url": "https://x/3.jpg",
     "permalink": "https://instagram.com/p/3", "timestamp": recent()},
    {"id": "4", "caption": "Old | sure", "media_type": "IMAGE", "media_url": "https://x/4.jpg",
     "permalink": "https://instagram.com/p/4", "timestamp": recent(60)},
]}


def run(cfg, graph):
    spec = {"id": "ig", "type": "instagram", "accounts": [{"username": "club_a", "venue": "Bolgia"}]}
    src = InstagramSource(spec, cfg, graph=graph, extractor=fake_extractor)
    return src, list(src.collect())


def test_confident_posts_go_straight_to_calendar_others_wait(tmp_path):
    cfg = make_root(tmp_path)
    src, events = run(cfg, FakeGraph(POSTS))
    assert [e.title for e in events] == ["Techno night"]
    ev = events[0]
    assert ev.venue.name == "Bolgia" and ev.venue.socials.instagram == "bolgia.official"  # registry handle wins over the posting account
    assert ev.artists[0].socials.instagram == "sample.dj"
    assert ev.end > ev.start  # 05:00 rolls over to the next day
    q = src._load_review()
    assert {i["status"] for i in q["items"].values()} == {"auto", "pending"}
    assert set(q["seen_posts"]) == {"1", "2", "3"}  # post 4 too old


def test_seen_posts_are_not_re_read_and_decisions_apply(tmp_path):
    cfg = make_root(tmp_path)
    run(cfg, FakeGraph(POSTS))
    (tmp_path / "config" / "ig_decisions.yaml").write_text(yaml.safe_dump({"approve": ["2-0"], "reject": ["1-0"]}))
    calls = []

    def counting(**kw):
        calls.append(kw)
        return fake_extractor(**kw)

    spec = {"id": "ig", "type": "instagram", "accounts": [{"username": "club_a", "venue": "Bolgia"}]}
    src = InstagramSource(spec, cfg, graph=FakeGraph(POSTS), extractor=counting)
    events = list(src.collect())
    assert calls == []
    assert [e.title for e in events] == ["Maybe party"]


def test_rejecting_removes_event_from_store(tmp_path):
    cfg = make_root(tmp_path)
    src, events = run(cfg, FakeGraph(POSTS))
    store = Store(tmp_path / "data" / "events.json")
    for e in events:
        cfg.enrich_venue(e.venue)
        store.upsert(e)
    assert len(store.all()) == 1
    (tmp_path / "config" / "ig_decisions.yaml").write_text("reject: ['1-0']\n")
    assert store.drop_sources(src.rejected_source_ids()) == 1 and store.all() == []


def test_review_page_lists_pending(tmp_path):
    cfg = make_root(tmp_path)
    src, _ = run(cfg, FakeGraph(POSTS))
    text = write_review_md(tmp_path, src._load_review()["items"], set(), set())
    assert "`2-0` **Maybe party**" in text and "Waiting for approval (1)" in text
