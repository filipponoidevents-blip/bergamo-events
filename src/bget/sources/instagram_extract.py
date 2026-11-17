"""Read an Instagram post (caption + flyer images) and pull out event details with Claude."""
from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Protocol

import requests
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class ExtractedArtist(BaseModel):
    name: str
    instagram: str | None = Field(None, description="Handle without @, only if tagged or written in the post")


class ExtractedEvent(BaseModel):
    title: str = Field(description="Event or party name; if none, the headliner(s) + venue")
    date: str = Field(description="YYYY-MM-DD of the night the event starts")
    start_time: str | None = Field(None, description="HH:MM 24h, if stated")
    end_time: str | None = Field(None, description="HH:MM 24h, if stated")
    late_end: bool = Field(False, description="True if the post says it goes on late (till late, fino all'alba) "
                           "without giving an end time")
    entry: str | None = Field(None, description="'paid' if the post mentions a price, list or tickets; 'free' if "
                              "entry is free for everyone; else null")
    fascia: str | None = Field(None, description="'preserata' if the post presents it as an aperitivo/preserata, "
                               "'serata' if as an after-hour / all-night party; else null")
    venue_name: str | None = None
    city: str | None = None
    artists: list[ExtractedArtist] = []
    genres: list[str] = Field(default=[], description="Music genres stated or clearly implied, lowercase")
    ticket_url: str | None = None
    price: str | None = None
    is_concert: bool = Field(False, description="True if this is a live band/singer concert rather than a DJ/club night")
    is_festival: bool = False
    kind: str | None = Field(None, description="One of: dj_club, live_music, comedy, quiz_games, food_drink, "
                             "culture, other")


class Extraction(BaseModel):
    is_event_announcement: bool = Field(
        description="True only if the post announces one or more specific upcoming events with a date")
    events: list[ExtractedEvent] = []
    confidence: float = Field(description="0-1: how sure you are that dates, venue and line-up are right")
    notes: str = Field("", description="Short note on anything ambiguous")


SYSTEM = """You read Instagram posts from clubs, promoters and DJs in Lombardy, Italy, and extract the \
upcoming events they announce. Posts are usually in Italian, sometimes English. The flyer image often has \
the real details (date, line-up, venue) while the caption has hashtags and handles; read both.

Rules:
- Resolve relative or partial dates ("sabato 22", "this Friday", "22.11") against the post date given. \
Events are in the future relative to the post date, never before it.
- A night that starts after midnight belongs to the previous evening's date only if the post says so; \
otherwise use the date written.
- If the post is a recap, a photo dump, a generic promo, a weekly residency without a specific date, or a \
merch/drink ad, set is_event_announcement to false and return no events.
- A post listing several dates (a tour or a monthly programme) yields one event per date.
- Only fill instagram handles that appear in the post (tags or @mentions). Never guess handles.
- If the posting account is a venue and no other venue is named, the event is at that venue."""


class Extractor(Protocol):
    def __call__(self, *, caption: str, image_urls: list[str], posted_at: datetime,
                 account: str, account_venue: str | None) -> Extraction: ...


class ClaudeExtractor:
    """Uses Claude with vision + structured outputs. Needs ANTHROPIC_API_KEY in the environment."""

    def __init__(self, model: str = "claude-haiku-5-5", effort: str = "low", max_images: int = 4):
        import anthropic  # imported lazily so the rest of the tool works without it

        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self.max_images = max_images

    @staticmethod
    def _image_block(url: str) -> dict | None:
        # Instagram CDN links are short-lived and sometimes refuse server-side fetches,
        # so download here and send the bytes.
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            log.warning("could not download image %s: %s", url[:80], e)
            return None
        media_type = r.headers.get("content-type", "image/jpeg").split(";")[0]
        if media_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
            media_type = "image/jpeg"
        return {"type": "image", "source": {
            "type": "base64", "media_type": media_type,
            "data": base64.standard_b64encode(r.content).decode()}}

    def __call__(self, *, caption, image_urls, posted_at, account, account_venue) -> Extraction:
        content: list[dict] = [b for u in image_urls[: self.max_images] if (b := self._image_block(u))]
        if not content:
            content.append({"type": "text", "text": "(Only the caption is available; the flyer image was not "
                            "provided. If key details are missing from the caption, lower your confidence.)"})
        content.append({"type": "text", "text": (
            f"Posting account: @{account}"
            + (f" (this account is the venue: {account_venue})" if account_venue else "")
            + f"\nPost date: {posted_at:%A %Y-%m-%d}\n\nCaption:\n{caption or '(no caption)'}"
        )})
        kwargs: dict = {}
        if not self.model.startswith("claude-haiku"):
            # server-side refusal fallback; not offered for Haiku models
            kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.effort},
            output_format=Extraction,
            **kwargs,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            log.warning("@%s: extraction returned no result (stop_reason=%s)", account, response.stop_reason)
            return Extraction(is_event_announcement=False, confidence=0, notes="no result from model")
        return response.parsed_output
