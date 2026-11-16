"""Phase 3 groundwork: turn a carousel into a ready-to-post bundle with captions and tags.

Nothing here posts anything. `build_bundle` writes a JSON that a publisher can send later.

Publishing constraints (from memory of the platform docs; re-verify before building):
- Instagram: Content Publishing via the Instagram Graph API needs an Instagram Business or
  Creator account (linked to a Facebook Page for the Facebook-login flow) and an app with
  instagram_content_publish. API carousels take up to 10 items; image
  children accept `user_tags` with usernames + x/y position. Images must be at a public URL.
- TikTok: Content Posting API supports photo posts (PHOTO media type, multiple images). Apps
  must pass TikTok's audit before posts can be public; unaudited apps post as private only.
  There is no structured user-tag field; @mentions go in the caption text.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Event

BASE_TAGS = ["#bergamo", "#bergamonightlife", "#techno", "#housemusic", "#techhouse", "#clubbing"]
IG_MAX_USER_TAGS_PER_IMAGE = 20
IG_CAPTION_MAX = 2200
IG_MAX_MENTIONS = 20  # Instagram caps @mentions per caption


def caption(events: list[Event], title: str, network: str = "instagram") -> str:
    lines = [title, ""]
    for e in events:
        handles = e.all_handles(network)
        tag = " ".join("@" + h for h in handles[:4])
        lines.append(f"▪️ {e.night.strftime('%d/%m')} {e.title} @ {e.venue.name} {tag}".rstrip())
    lines += ["", " ".join(BASE_TAGS)]
    text = "\n".join(lines)
    return text[:IG_CAPTION_MAX]


def user_tags_for_slide(events: list[Event]) -> list[dict]:
    """Instagram image user_tags: spread tags down the slide, near each event's row."""
    tags, y = [], 0.2
    for e in events:
        for h in e.all_handles("instagram")[:3]:
            if len(tags) >= IG_MAX_USER_TAGS_PER_IMAGE:
                return tags
            tags.append({"username": h, "x": 0.5, "y": round(min(y, 0.95), 2)})
        y += 0.15
    return tags


def build_bundle(slide_paths: list[Path], slide_events: list[list[Event]], title: str, out: Path) -> Path:
    """slide_events[i] = events shown on slide i (empty for cover slides)."""
    all_events = [e for evs in slide_events for e in evs]
    bundle = {
        "title": title,
        "instagram": {
            "caption": caption(all_events, title, "instagram"),
            "children": [
                {"image": str(p), "user_tags": user_tags_for_slide(evs)}
                for p, evs in zip(slide_paths, slide_events)
            ],
        },
        "tiktok": {
            "caption": caption(all_events, title, "tiktok"),
            "photos": [str(p) for p in slide_paths],
        },
        "missing_handles": sorted({
            f"{kind}:{name}"
            for e in all_events
            for kind, name, s in [("venue", e.venue.name, e.venue.socials),
                                  *[("artist", a.name, a.socials) for a in e.artists]]
            if not s.instagram
        }),
    }
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2))
    return out
