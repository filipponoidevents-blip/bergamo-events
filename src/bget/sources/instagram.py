"""Instagram auto-check.

Every run:
1. For each watched account, read its latest posts through the Instagram Graph API
   "Business Discovery" endpoint (works for public Business/Creator accounts only).
2. Each post not seen before is read by the caption reader: the free pattern matcher
   (instagram_rules.py, default) or Claude (instagram_extract.py, reader: claude).
3. Every extracted event lands in the review queue (data/ig_review.json, written by the bot).
   - confidence >= auto_approve_confidence  -> goes on the calendar straight away
   - lower confidence                       -> waits for approval
   Filippo approves or rejects by id in config/ig_decisions.yaml (the bot never edits that file).
4. Approved events are yielded like any other source; rejected ones are removed from the store.

Needs these environment variables (GitHub Actions secrets):
  IG_USER_ID        id of OUR Instagram professional account (the one making the API calls)
  IG_ACCESS_TOKEN   long-lived token with instagram_basic + pages_show_list (+ business discovery)
  ANTHROPIC_API_KEY only if instagram.reader is "claude" (default "rules" is free)
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
from .categories import CATEGORIES, categorize
from .instagram_extract import ClaudeExtractor, Extraction, Extractor
from . import ocr
from .instagram_rules import RE_DATE_LINE, _strip_date_words, _title
from .instagram_rules import ListingExtractor, RulesExtractor

log = logging.getLogger(__name__)

MEDIA_FIELDS = "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,children{media_type,media_url}"


class GraphClient:
    def __init__(self, user_id: str, token: str, version: str = "v27.0"):
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
        self.calendar_kinds = set(ig.get("calendar_categories", ["dj_club"]))
        self.review_path = cfg.root / "data" / "ig_review.json"
        self.decisions_path = cfg.root / "config" / "ig_decisions.yaml"
        self._graph = graph
        self._extractor = extractor
        self._ig = ig
        self.token_problem: str | None = None

    # lazily built so a run with the source disabled never needs credentials
    @property
    def graph(self) -> GraphClient:
        if self._graph is None:
            uid = os.environ.get("IG_USER_ID") or str(self._ig.get("user_id") or "")
            tok = os.environ.get("IG_ACCESS_TOKEN")
            if not uid or not tok:
                raise RuntimeError("IG_ACCESS_TOKEN secret (or instagram.user_id) is not set")
            self._graph = GraphClient(uid, tok, self._ig.get("graph_version", "v27.0"))
        return self._graph

    @property
    def listing_extractor(self) -> Extractor:
        if not hasattr(self, "_listing"):
            self._listing = ListingExtractor(self.cfg)
        return self._listing

    @property
    def extractor(self) -> Extractor:
        if self._extractor is None:
            if self._ig.get("reader", "rules") == "claude":
                self._extractor = ClaudeExtractor(
                    model=self._ig.get("model", "claude-haiku-5-5"), effort=self._ig.get("effort", "low"))
            else:
                self._extractor = RulesExtractor(self.cfg)
        return self._extractor

    # ---- review queue -----------------------------------------------------
    def _load_review(self) -> dict[str, Any]:
        if self.review_path.exists():
            return json.loads(self.review_path.read_text())
        return {"seen_posts": {}, "items": {}}

    def _save_review(self, q: dict[str, Any]) -> None:
        self.review_path.parent.mkdir(parents=True, exist_ok=True)
        self.review_path.write_text(json.dumps(q, ensure_ascii=False, indent=2, sort_keys=True))

    def title_overrides(self) -> dict[str, str]:
        """titles: {id: "New name"} in ig_decisions.yaml renames a night."""
        if not self.decisions_path.exists():
            return {}
        d = yaml.safe_load(self.decisions_path.read_text()) or {}
        return {str(k): str(v) for k, v in (d.get("titles") or {}).items()}

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
        accounts = self.spec.get("accounts", [])
        # Meta caps requests per hour for apps in development mode, so each run checks a
        # rotating slice of the list (instagram.accounts_per_run) and resumes where it stopped.
        per_run = self._ig.get("accounts_per_run") or len(accounts)
        start = q.get("next_account", 0) % max(len(accounts), 1)
        batch = (accounts[start:] + accounts[:start])[:per_run]
        checked = 0
        for acc in batch:
            acc = {"username": acc} if isinstance(acc, str) else acc
            user = acc["username"].lstrip("@")
            try:
                posts = self.graph.recent_media(user, self.posts_per_account)
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                if "Invalid user id" in str(e) or "does not exist" in str(e) or "(#110)" in str(e):
                    q.setdefault("unreadable_accounts", {})[user] = str(e).split(": ", 1)[-1][:120]
                log.warning("instagram: %s", e)
                if any(k in str(e) for k in ("(#190)", "Error validating access token", "Session has expired",
                                             "IG_ACCESS_TOKEN", "(#10)")):
                    self.token_problem = str(e)
                    break  # same failure for every account: stop early
                if "(#4)" in str(e) or "(#17)" in str(e) or "(#32)" in str(e):
                    errors.pop()  # not this account's fault
                    log.warning("instagram: Meta request limit reached after %d accounts; resuming next run", checked)
                    break
                checked += 1
                continue
            checked += 1
            q.get("unreadable_accounts", {}).pop(user, None)
            for post in posts:
                pid = post["id"]
                posted = datetime.fromisoformat(post["timestamp"].replace("+0000", "+00:00"))
                if pid in q["seen_posts"] or now - posted > self.max_age:
                    continue
                try:
                    caption = post.get("caption", "") or ""
                    images = _images(post)[:4]
                    use_ocr = acc.get("ocr", self._ig.get("ocr", False)) or acc.get("listing")
                    if use_ocr and ocr.available():
                        text = "\n".join(t for u in images if (t := ocr.ocr_url(u)))
                        caption = f"{caption}\n\n{text}".strip()
                    elif use_ocr:
                        log.warning("instagram: OCR requested for @%s but tesseract is not installed", user)
                    extractor = self.listing_extractor if acc.get("listing") else self.extractor
                    post["_text"] = caption  # caption + flyer text, kept as the event's description
                    ex = extractor(caption=caption,
                                   image_urls=images if self._ig.get("read_images", False) else [],
                                   posted_at=posted, account=user, account_venue=acc.get("venue"))
                except Exception as e:  # noqa: BLE001 - retry this post next run
                    log.warning("instagram: extraction failed for %s: %s", post.get("permalink"), e)
                    continue
                q["seen_posts"][pid] = {"account": user, "at": post["timestamp"],
                                        "event": ex.is_event_announcement}
                self._queue(q, ex, post, user, acc.get("venue"))
        if accounts:
            q["next_account"] = (start + checked) % len(accounts)
        self._save_review(q)
        if errors and checked and len(errors) == checked:
            raise RuntimeError("every Instagram account failed; last error: " + errors[-1])
        yield from self.approved_events(q)

    def _queue(self, q, ex: Extraction, post, user, account_venue) -> None:
        if not ex.is_event_announcement:
            return
        caption = (post.get("_text") or post.get("caption") or "")[:1200]
        today = datetime.now(timezone.utc).date().isoformat()
        for i, e in enumerate(ex.events):
            if e.date < today:  # already happened: nothing to put on the calendar
                continue
            item_id = f"{post['id']}-{i}"
            kind = e.kind if e.kind in CATEGORIES else categorize(
                f"{e.title} {' '.join(e.genres)} {caption}", self._venue_kind(e.venue_name or account_venue))
            if kind not in self.calendar_kinds:
                status = "info"  # kept for reference (other_events.md / other.ics), never on the main calendar
            elif ex.confidence >= self.auto_conf:
                status = "auto"
            else:
                status = "pending"
            q["items"][item_id] = {
                "id": item_id,
                "kind": kind,
                "caption": caption,
                "status": status,
                "confidence": round(ex.confidence, 2),
                "notes": ex.notes,
                "account": user,
                "account_venue": account_venue,
                "permalink": post.get("permalink"),
                "image": (_images(post) or [None])[0],
                "event": e.model_dump(),
            }

    def _venue_kind(self, name: str | None) -> str | None:
        v = self.cfg.match_venue(name) if name else None
        return v.kind if v else None

    def approved_events(self, q) -> Iterable[Event]:
        approve, reject = self.decisions()
        tz = ZoneInfo(self.cfg.settings.get("timezone", "Europe/Rome"))
        for item_id, it in q["items"].items():
            ok = (it["status"] == "auto" or (item_id in approve and it["status"] != "info")) \
                and item_id not in reject
            if ok:
                try:
                    yield self.to_event(it, tz)
                except ValueError as e:
                    log.warning("instagram: bad date in %s: %s", item_id, e)

    def rejected_source_ids(self) -> set[str]:
        """Rejected by Filippo, or not a club night (kept as info only): off the calendar."""
        info = {i for i, it in self._load_review().get("items", {}).items() if it.get("status") == "info"}
        return {f"ig:{x}" for x in self.decisions()[1] | info}

    def to_event(self, it: dict, tz: ZoneInfo) -> Event:
        e = dict(it["event"])
        if (t := self.title_overrides().get(it["id"])):
            e["title"] = t
        elif it.get("caption") and RE_DATE_LINE.search(e.get("title") or "") and \
                len(_strip_date_words(e.get("title") or "")) < 4:
            # read before the title rule improved: take the name from the stored caption
            e["title"] = _title(it["caption"]) or e["title"]
        start = datetime.fromisoformat(f"{e['date']}T{e.get('start_time') or '23:00'}").replace(tzinfo=tz)
        if start.hour < 6:
            # "sabato 21, dalle 00:30" means the night of Saturday 21, i.e. Sunday 00:30
            start += timedelta(days=1)
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
            description=it.get("caption") or "",
            category=(Category.CONCERT if e.get("is_concert") or it.get("kind") == "live_music"
                      else Category.FESTIVAL if e.get("is_festival") else Category.CLUB),
            url=it.get("permalink"), ticket_url=e.get("ticket_url"), image_url=it.get("image"), price=e.get("price"),
            promoter=promoter, promoter_name=None if it.get("account_venue") else "@" + it["account"],
            sources=[f"ig:{it['id']}"], source_urls=[it.get("permalink")] if it.get("permalink") else [],
        )


def write_review_md(root: Path, items: dict[str, Any], approve: set[str], reject: set[str],
                    unreadable: dict[str, str] | None = None) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    lines = ["# Instagram events waiting for review", "",
             "To approve or reject, add the id under `approve:` or `reject:` in `config/ig_decisions.yaml`.",
             "Auto-approved ones are already on the calendar; reject them the same way if they are wrong.", ""]
    rows = sorted(items.values(), key=lambda x: x["event"]["date"])
    for title, cond in [("Waiting for approval", lambda it: it["status"] == "pending"),
                        ("Auto-approved", lambda it: it["status"] == "auto")]:
        sel = [it for it in rows if cond(it) and it["id"] not in approve | reject and it["event"]["date"] >= today]
        lines += [f"## {title} ({len(sel)})", ""]
        sel.sort(key=lambda it: (list(CATEGORIES).index(it.get("kind", "other")), it["event"]["date"]))
        last_kind = None
        for it in sel:
            if it.get("kind", "other") != last_kind:
                last_kind = it.get("kind", "other")
                lines += ["", f"**{CATEGORIES[last_kind]}**", ""]
            e = it["event"]
            who = ", ".join(a["name"] for a in e.get("artists", [])) or "-"
            lines.append(f"- `{it['id']}` **{e['title']}**, {e['date']} {e.get('start_time') or ''} at "
                         f"{e.get('venue_name') or it.get('account_venue') or '?'} ({e.get('city') or '?'}). "
                         f"Line-up: {who}. Confidence {it['confidence']}. From @{it['account']} {it.get('permalink') or ''}"
                         + (f" _Note: {it['notes']}_" if it.get("notes") else ""))
        lines.append("")
    n_info = sum(1 for it in rows if it["status"] == "info" and it["event"]["date"] >= today)
    if n_info:
        lines += [f"_{n_info} non-club events (live, comedy, quiz, food, culture...) are listed in other_events.md "
                  "and other.ics, not on the main calendar._", ""]
    if unreadable:
        lines += ["## Accounts Instagram won't let us read", "",
                  "Usually a changed handle, or a personal/private account. Fix or remove them in "
                  "`config/sources.yaml`.", ""]
        lines += [f"- @{u}: {why}" for u, why in sorted(unreadable.items())] + [""]
    text = "\n".join(lines)
    (root / "out").mkdir(exist_ok=True)
    (root / "out" / "review.md").write_text(text)
    return text


def other_events(cfg, items: dict[str, Any], tz) -> list[tuple[str, Event]]:
    """Upcoming non-club events kept for reference: (category, Event)."""
    src = InstagramSource({"id": "ig", "accounts": []}, cfg)
    today = datetime.now(timezone.utc).date().isoformat()
    out = []
    for it in items.values():
        if it["status"] == "info" and it["event"]["date"] >= today:
            try:
                ev = src.to_event(it, tz)
            except ValueError:
                continue
            cfg.enrich_venue(ev.venue)
            out.append((it.get("kind", "other"), ev))
    return sorted(out, key=lambda x: x[1].start)


def write_other_events(root: Path, others: list[tuple[str, Event]]) -> None:
    from ..calendar_ics import build_ics

    lines = ["# Other events (not on the main calendar)", "",
             "Found on Instagram but not DJ/club nights. Kept for reference.", ""]
    for kind in CATEGORIES:
        sel = [e for k, e in others if k == kind]
        if not sel:
            continue
        lines += [f"## {CATEGORIES[kind]} ({len(sel)})", ""]
        lines += [f"- {e.start:%a %d/%m %H:%M} **{e.title}** at {e.venue.name}. {e.url or ''}" for e in sel]
        lines.append("")
    (root / "out").mkdir(exist_ok=True)
    (root / "out" / "other_events.md").write_text("\n".join(lines))
    (root / "out" / "other.ics").write_text(
        build_ics([e for _, e in others], "Bergamo Nights · Other events", prefix=""))
