"""Generic schema.org Event extractor.

Most ticketing platforms (Dice, Xceed, Shotgun, Eventbrite, Ticketone) and many venue sites
embed <script type="application/ld+json"> with @type Event/MusicEvent/DanceEvent/Festival,
so one adapter covers a lot of sources without site-specific scraping.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Artist, Category, Event, Socials, Venue
from .base import Source

log = logging.getLogger(__name__)
EVENT_TYPES = {"Event", "MusicEvent", "DanceEvent", "Festival", "SocialEvent"}
IG = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)")
TT = re.compile(r"tiktok\.com/@([A-Za-z0-9_.]+)")
RA = re.compile(r"ra\.co/(?:dj|clubs|promoters)/([A-Za-z0-9_-]+)")


def socials_from_sameas(same_as: Any) -> Socials:
    s = Socials()
    for url in same_as if isinstance(same_as, list) else [same_as] if same_as else []:
        if m := IG.search(url):
            s.instagram = m.group(1).rstrip("/")
        elif m := TT.search(url):
            s.tiktok = m.group(1)
        elif "facebook.com" in url:
            s.facebook = url
        elif m := RA.search(url):
            s.resident_advisor = m.group(1)
        elif not s.website:
            s.website = url
    return s


def _walk(node: Any) -> Iterable[dict]:
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = set(t if isinstance(t, list) else [t])
        if types & EVENT_TYPES:
            yield node
        for k in ("@graph", "itemListElement", "item", "subEvent"):
            if k in node:
                yield from _walk(node[k])


class JsonLdSource(Source):
    type = "jsonld"

    def _urls(self) -> list[str]:
        """URLs may hold a date template, e.g. ".../data/musica/{date:%Y/%m/%d}/", expanded for each of
        the next `days` days (default 14), for agendas that list one day per page."""
        from datetime import date, timedelta
        out = []
        for url in self.spec.get("urls", []):
            if "{date" in url:
                today = date.today()
                out += [url.format(date=today + timedelta(days=i)) for i in range(self.spec.get("days", 14))]
            else:
                out.append(url)
        return out

    def collect(self) -> Iterable[Event]:
        """Parse each URL. With `follow: <regex>` in the spec, the URLs are listing pages:
        links matching the regex are opened (up to `max_pages`) and parsed as event pages."""
        follow = re.compile(self.spec["follow"]) if self.spec.get("follow") else None
        max_pages = self.spec.get("max_pages", 40)
        seen: set[str] = set()
        for url in self._urls():
            try:
                html = self.fetch(url)
            except Exception as e:  # noqa: BLE001 - one bad page must not kill the run
                log.warning("%s: failed to fetch %s: %s", self.id, url, e)
                continue
            yield from self.parse(html, url)
            if not follow:
                continue
            for link in self.links(html, url, follow):
                if link in seen or len(seen) >= max_pages:
                    continue
                seen.add(link)
                try:
                    yield from self.parse(self.fetch(link), link)
                except Exception as e:  # noqa: BLE001
                    log.warning("%s: failed to fetch %s: %s", self.id, link, e)

    @staticmethod
    def links(html: str, base: str, pattern: re.Pattern) -> list[str]:
        from urllib.parse import urljoin, urldefrag

        out: list[str] = []
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            u = urldefrag(urljoin(base, a["href"]))[0]
            if pattern.search(u) and u not in out:
                out.append(u)
        return out

    def parse(self, html: str, page_url: str) -> Iterable[Event]:
        tz = ZoneInfo(self.cfg.settings.get("timezone", "Europe/Rome"))
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "")
            except json.JSONDecodeError:
                continue
            for node in _walk(data):
                try:
                    yield self._to_event(node, page_url, tz)
                except Exception as e:  # noqa: BLE001
                    log.warning("%s: skipped malformed event on %s: %s", self.id, page_url, e)

    def _to_event(self, n: dict, page_url: str, tz: ZoneInfo) -> Event:
        loc = n.get("location") or {}
        if isinstance(loc, list):
            loc = loc[0]
        addr = loc.get("address") or {}
        if isinstance(addr, str):
            addr = {"streetAddress": addr}
        geo = loc.get("geo") or {}
        venue = Venue(
            name=loc.get("name", "Unknown venue"),
            city=addr.get("addressLocality", ""),
            address=addr.get("streetAddress"),
            lat=float(geo["latitude"]) if geo.get("latitude") else None,
            lon=float(geo["longitude"]) if geo.get("longitude") else None,
            socials=socials_from_sameas(loc.get("sameAs")),
        )

        def dt(v: str | None) -> datetime | None:
            if not v:
                return None
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=tz)

        performers = n.get("performer") or []
        if isinstance(performers, dict):
            performers = [performers]
        artists = [
            Artist(name=p.get("name", ""), socials=socials_from_sameas(p.get("sameAs")),
                   genres=[p["genre"]] if isinstance(p.get("genre"), str) else p.get("genre", []))
            for p in performers if p.get("name")
        ]
        org = n.get("organizer") or {}
        if isinstance(org, list):
            org = org[0] if org else {}
        offers = n.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        t = n.get("@type")
        types = set(t if isinstance(t, list) else [t])
        category = (Category.FESTIVAL if "Festival" in types
                    else Category.CLUB if "DanceEvent" in types else Category.OTHER)
        image = n.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url")
        genre = n.get("genre") or []
        return Event(
            title=n.get("name", "").strip(),
            start=dt(n.get("startDate")),
            end=dt(n.get("endDate")),
            venue=venue,
            artists=artists,
            genres=[genre] if isinstance(genre, str) else list(genre),
            category=category,
            description=BeautifulSoup(n.get("description", ""), "html.parser").get_text(" ").strip()[:2000],
            url=n.get("url") or page_url,
            ticket_url=offers.get("url"),
            image_url=image,
            price=f'{offers.get("price")} {offers.get("priceCurrency", "")}'.strip() if offers.get("price") else None,
            promoter_name=org.get("name"),
            promoter=socials_from_sameas(org.get("sameAs")) if org else None,
            sources=[self.id],
            source_urls=[page_url],
        )
