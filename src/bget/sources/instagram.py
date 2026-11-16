"""Instagram auto-check.

Every run:
1. For each watched account, read its latest posts through the Instagram Graph API
   "Business Discovery" endpoint (works for public Business/Creator accounts only).
2. Each post not seen before goes to Claude, which reads the caption and the flyer images
   and returns structured event details (see instagram_extract.py).
3. Every extracted event lands in the review queue (data/ig_review.json, written by the bot).
   - confidence >= auto_approve_confidence  -> goes on the calendar straight away
   - lower confidence                       -> waits for approval
   Filippo approves or rejects by id in config/ig_decisions.yaml (the bot never edits that file).
4. Approved events are yielded like any other source; rejected ones are removed from the store.

Needs these environment variables (GitHub Actions secrets):
  IG_USER_ID        id of OUR Instagram professional account (the one making the API calls)
  IG_ACCESS_TOKEN   long-lived token with instagram_basic + pages_show_list (+ business discovery)
  ANTHROPIC_API_KEY for the flyer/caption reader
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests
import yaml

from ..models import Artist, Category, Event, Socials, Venue
from .base import USER_AGENT, Source
from .instagram_extract import ClaudeExtractor, Extraction, Extractor

log = logging.getLogger(__name__)

MEDIA_FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,children{media_type,media_url}"


class GraphClient:
    def __init__(self, user_id: str, token: str, version: str = "v23.0"):
        self.user_id, self.token, self.version = user_id, token, version

    def recent_media(self, username: str, limit: int = 12) -> list[dict[str, Any]]:
        fields = f"business_discovery.username({username}){{username,media.limit({limit}){{{MEDIA_FIELDS}}}}}"
        r = requests.get(
            f"https://graph.facebook.com/{self.version}/{self.user_id}",
            params={"fields": fields, "access_token": self.token},
            headers={"User-Agent": USER_AGENT}, timeout=30,
        )
        if r.status_code != 200:
            # Typical causes: personal (non-business) account, private account, renamed handle.
            msg = r.json().get("error", {}).get("message", r.text[:200]) if r.headers.get(
                "content-type", "").startswith("application/json") else r.text[:200]
            raise RuntimeError(f"@{username}: {msg}")
        return r.json().get("business_discovery", {}).get("media", {}).get("data", [])


def _images(post: dict) -> list[str]:
    if post.get("media_type") == "CAROUSEL_ALBUM":
        kids = post.get("children", {}).get("data", [])
        return [k["media_url"] for k in kids if k.get("media_type") == "IMAGE" and k.get("media_url")]
    if post.get("media_type") == "VIDEO":
        return [post["thumbnail_url"]] if post.get("thumbnail_url") else []
    return [post["media_url"]] if post.get("media_url") else []


class InstagramSource(Source):
    type = "instagram"

    def __init__(self, spec, cfg, graph: GraphClient | None = None, extractor: Extractor | None = None):
        super().__init__(spec, cfg)
        ig = cfg.settings.get("instagram", {})
        self.max_age = timedelta(days=ig.get("max_post_age_days", 21))
        self.posts_per_account = ig.get("posts_per_account", 12)
        self.auto_conf = ig.get("auto_approve_confidence", 0.85)
        self.review_path = cfg.root / "data" / "ig_review.json"
        self.decisions_path = cfg.root / "config" / "ig_decisions.yaml"
        self._graph = graph
        self._extractor = extractor
        self._ig = ig

    # lazily built so a run with the source disabled never needs credentials
    @property
    def graph(self) -> GraphClient:
        if self._graph is None:
            uid, tok = os.environ.get("IG_USER_ID"), os.environ.get("IG_ACCESS_TOKEN")
            if not uid or not tok:
                raise RuntimeError("IG_USER_ID / IG_ACCESS_TOKEN are not set")
            self._graph = GraphClient(uid, tok, self._ig.get("graph_version", "v23.0"))
        return self._graph

    @property
    def extractor(self) -> Extractor:
        if self._extractor is None:
            self._extractor = ClaudeExtractor(
                model=self._ig.get("model", "claude-haiku-5-5"), effort=self._ig.get("effort", "low"))
        return self._extractor

    # ---- review queue -----------------------------------------------------
    def _load_review(self) -> dict[str, Any]:
        if self.review_path.exists():
            return json.loads(self.review_path.read_text())
        return {"seen_posts": {}, "items": {}}

    def _save_review(self, q: dict[str, Any]) -> None:
        self.review_path.parent.mkdir(parents=True, exist_ok=True)
        self.review_path.write_text(json.dumps(q, ensure_ascii=False, indent=2, sort_keys=True))

    def decisions(self) -> tuple[set[str], set[str]]:
        if not self.decisions_path.exists():
            return set(), set()
        d = yaml.safe_load(self.decisions_path.read_text()) or {}
        return {str(x) for x in d.get("approve") or []}, {str(x) for x in d.get("reject") or []}

    # ---- main -------------------------------------------------------------
    def collect(self) -> Iterable[Event]:
        q = self._load_review()
        now = datetime.now(timezone.utc)
        errors = []
        for acc in self.spec.get("accounts", []):
            acc = {"username": acc} if isinstance(acc, str) else acc
            user = acc["username"].lstrip("@")
            try:
                posts = self.graph.recent_media(user, self.posts_per_account)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                log.warning("instagram: %s", e)
                continue
            for post in posts:
                pid = post["id"]
                posted = datetime.fromisoformat(post["timestamp"].replace("+0000", "+00:00"))
                if pid in q["seen_posts"] or now - posted > self.max_age:
                    continue
                try:
                    images = _images(post) if self._ig.get("read_images", False) else []
                    ex = self.extractor(caption=post.get("caption", ""), image_urls=images,
                                        posted_at=posted, account=user, account_venue=acc.get("venue"))
                except Exception as e:  # noqa: BLE001 - retry this post next run
                    log.warning("instagram: extraction failed for %s: %s", post.get("permalink"), e)
                    continue
                q["seen_posts"][pid] = {"account": user, "at": post["timestamp"],
                                        "event": ex.is_event_announcement}
                self._queue(q, ex, post, user, acc.get("venue"))
        self._save_review(q)
        if errors and len(errors) == len(self.spec.get("accounts", [])):
            raise RuntimeError("every Instagram account failed; last error: " + errors[-1])
        yield from self.approved_events(q)

    def _queue(self, q, ex: Extraction, post, user, account_venue) -> None:
        if not ex.is_event_announcement:
            return
        for i, e in enumerate(ex.events):
            item_id = f"{post['id']}-{i}"
            q["items"][item_id] = {
                "id": item_id,
                "status": "auto" if ex.confidence >= self.auto_conf else "pending",
                "confidence": round(ex.confidence, 2),
                "notes": ex.notes,
                "account": user,
                "account_venue": account_venue,
                "permalink": post.get("permalink"),
                "image": (_images(post) or [None])[0],
                "event": e.model_dump(),
            }

    def approved_events(self, q) -> Iterable[Event]:
        approve, reject = self.decisions()
        tz = ZoneInfo(self.cfg.settings.get("timezone", "Europe/Rome"))
        for item_id, it in q["items"].items():
            ok = (it["status"] == "auto" or item_id in approve) and item_id not in reject
            if ok:
                try:
                    yield self.to_event(it, tz)
                except ValueError as e:
                    log.warning("instagram: bad date in %s: %s", item_id, e)

    def rejected_source_ids(self) -> set[str]:
        return {f"ig:{x}" for x in self.decisions()[1]}

    def to_event(self, it: dict, tz: ZoneInfo) -> Event:
        e = it["event"]
        start = datetime.fromisoformat(f"{e['date']}T{e.get('start_time') or '23:00'}").replace(tzinfo=tz)
        end = None
        if e.get("end_time"):
            end = datetime.fromisoformat(f"{e['date']}T{e['end_time']}").replace(tzinfo=tz)
            if end <= start:
                end += timedelta(days=1)
        venue_name = e.get("venue_name") or it.get("account_venue") or "Unknown venue"
        venue = self.cfg.match_venue(venue_name, e.get("city")) or Venue(name=venue_name, city=e.get("city") or "")
        if it.get("account_venue") and venue.name == it["account_venue"] and not venue.socials.instagram:
            venue.socials.instagram = it["account"]
        promoter = None if it.get("account_venue") else Socials(instagram=it["account"])
        return Event(
            title=e["title"], start=start, end=end, venue=venue,
            artists=[Artist(name=a["name"], socials=Socials(instagram=a.get("instagram"))) for a in e.get("artists", [])],
            genres=e.get("genres", []),
            category=Category.CONCERT if e.get("is_concert") else Category.FESTIVAL if e.get("is_festival") else Category.CLUB,
            url=it.get("permalink"), ticket_url=e.get("ticket_url"), image_url=it.get("image"), price=e.get("price"),
            promoter=promoter, promoter_name=None if it.get("account_venue") else "@" + it["account"],
            sources=[f"ig:{it['id']}"], source_urls=[it.get("permalink")] if it.get("permalink") else [],
        )


def write_review_md(root: Path, items: dict[str, Any], approve: set[str], reject: set[str]) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    lines = ["# Instagram events waiting for review", "",
             "To approve or reject, add the id under `approve:` or `reject:` in `config/ig_decisions.yaml`.",
             "Auto-approved ones are already on the calendar; reject them the same way if they are wrong.", ""]
    rows = sorted(items.values(), key=lambda x: x["event"]["date"])
    for title, cond in [("Waiting for approval", lambda it: it["status"] == "pending"),
                        ("Auto-approved", lambda it: it["status"] == "auto")]:
        sel = [it for it in rows if cond(it) and it["id"] not in approve | reject and it["event"]["date"] >= today]
        lines += [f"## {title} ({len(sel)})", ""]
        for it in sel:
            e = it["event"]
            who = ", ".join(a["name"] for a in e.get("artists", [])) or "-"
            lines.append(f"- `{it['id']}` **{e['title']}**, {e['date']} {e.get('start_time') or ''} at "
                         f"{e.get('venue_name') or it.get('account_venue') or '?'} ({e.get('city') or '?'}). "
                         f"Line-up: {who}. Confidence {it['confidence']}. From @{it['account']} {it.get('permalink') or ''}"
                         + (f" _Note: {it['notes']}_" if it.get("notes") else ""))
        lines.append("")
    text = "\n".join(lines)
    (root / "out").mkdir(exist_ok=True)
    (root / "out" / "review.md").write_text(text)
    return text
