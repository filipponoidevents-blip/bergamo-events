"""Local press articles (e.g. Prima Bergamo's "eventi" section): read each new article like an
Instagram caption with the free rules reader, and keep only DJ/club nights at a known venue."""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Category, Event, Venue
from .base import Source
from .instagram_rules import RulesExtractor

log = logging.getLogger(__name__)

# words in the listing teaser that make an article worth opening
CLUBBY = re.compile(r"clubbing|discotec|disco|dj|techno|house|party|serata|nottat|notte|ballare|si balla|rave|"
                    r"festival|capodanno|halloween|club", re.I)


ROUNDUP = re.compile(r"cosa fare|che fare|idee per|weekend a bergamo|eventi del weekend|appuntamenti del|"
                     r"agenda|da non perdere|guida", re.I)


class PressSource(Source):
    type = "press"

    def collect(self) -> Iterable[Event]:
        tz = ZoneInfo(self.cfg.settings.get("timezone", "Europe/Rome"))
        reader = RulesExtractor(self.cfg)
        link_rx = re.compile(self.spec["follow"])
        max_articles = self.spec.get("max_articles", 25)
        opened = 0
        for url in self.spec.get("urls", []):
            try:
                soup = BeautifulSoup(self.fetch(url), "html.parser")
            except Exception as e:  # noqa: BLE001
                log.warning("%s: listing %s failed: %s", self.id, url, e)
                continue
            for a in soup.find_all("a", href=True):
                href, teaser = a["href"], a.get_text(" ", strip=True)
                if not link_rx.search(href) or not CLUBBY.search(teaser) or opened >= max_articles:
                    continue
                opened += 1
                try:
                    yield from self._article(href, reader, tz)
                except Exception as e:  # noqa: BLE001
                    log.warning("%s: article %s failed: %s", self.id, href, e)

    def _article(self, url: str, reader: RulesExtractor, tz: ZoneInfo) -> Iterable[Event]:
        soup = BeautifulSoup(self.fetch(url), "html.parser")
        title, body, published = article_parts(soup)
        if ROUNDUP.search(title):
            return  # "cosa fare nel weekend" lists many places: one venue can't be told from another
        if not published or datetime.now(tz) - published > timedelta(days=self.spec.get("max_age_days", 30)):
            return
        ex = reader(caption=f"{title}\n{body}", image_urls=[], posted_at=published,
                    account=self.id, account_venue=None)
        for e in ex.events:
            log.info("%s: %s -> %s %s at %s (%s, %.2f)", self.id, title[:60], e.date, e.start_time,
                     e.venue_name, e.kind, ex.confidence)
            if e.date < datetime.now(tz).date().isoformat():
                continue
            if e.kind != "dj_club" or not e.venue_name or ex.confidence < self.spec.get("min_confidence", 0.6):
                continue
            start = datetime.fromisoformat(f"{e.date}T{e.start_time or '23:00'}").replace(tzinfo=tz)
            if start.hour < 6:
                start += timedelta(days=1)
            venue = self.cfg.match_venue(e.venue_name) or Venue(name=e.venue_name, city="")
            yield Event(title=title[:80], start=start, venue=venue, genres=e.genres,
                        category=Category.CLUB, description=body[:1200], url=url,
                        sources=[f"press:{self.id}"], source_urls=[url])


def article_parts(soup: BeautifulSoup) -> tuple[str, str, datetime | None]:
    """Headline, text and publication time, from JSON-LD when present, else from the page."""
    title, body, published = "", "", None
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "", strict=False)
        except ValueError:
            continue
        for d in (data if isinstance(data, list) else data.get("@graph", [data])):
            if isinstance(d, dict) and str(d.get("@type", "")).endswith("Article"):
                title = title or d.get("headline", "")
                body = body or d.get("articleBody", "")
                if d.get("datePublished"):
                    published = published or datetime.fromisoformat(d["datePublished"].replace("Z", "+00:00"))
    if not title and (m := soup.find("meta", property="og:title")):
        title = m.get("content", "")
    if not published and (m := soup.find("meta", property="article:published_time")):
        published = datetime.fromisoformat(m["content"].replace("Z", "+00:00"))
    if not body:
        node = soup.find("article") or soup.find(class_=re.compile("entry-content|article-body|post-content"))
        if node:
            body = "\n".join(p.get_text(" ", strip=True) for p in node.find_all(["p", "h2", "h3", "li"]))
    return html.unescape(title).strip(), html.unescape(body).strip(), published
