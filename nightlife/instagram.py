"""Instagram: legge i post dei profili seguiti e scopre da solo profili nuovi (locali, promoter, DJ).

Usa l'API ufficiale gratuita "Business Discovery" di Meta: funziona con profili pubblici Business o Creator.
Servono due segreti su GitHub: IG_ACCESS_TOKEN e (facoltativo) IG_USER_ID.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from .city import City, clean_name
from .util import UA, now_iso, plain

log = logging.getLogger("nightlife.instagram")
PROFILE = "username,name,biography,website,followers_count,media_count"
MEDIA = "id,caption,media_type,permalink,timestamp,media_url,thumbnail_url"

VENUE_WORDS = re.compile(r"\b(?:club|discoteca|disco\s*(?:bar|pub|club)|lounge|cocktail|bar\b|pub\b|bistrot?|caf[eè]|caffetteria|ristorante|pizzeria|birreria|brewery|live\s*music\s*(?:club|venue)|locale|rooftop|circolo|teatro|spazio|venue|beach|lido|chiosco|osteria|enoteca)", re.I)
ADDRESS_WORDS = re.compile(r"\b(?:via|viale|piazza|piazzale|largo|corso|strada|localit[aà])\s+\w+|📍", re.I)
PROMOTER_WORDS = re.compile(r"\b(?:party|parties|event[is]?|eventi|serat[ae]|format|nights?|crew|collective|collettivo|organizzazion|one\s*night|rave|festival|clubbing|community|label|movement|project|production)", re.I)
ARTIST_WORDS = re.compile(r"\b(?:dj|producer|artist[a]?|musician|musicista|singer|cantante|rapper|vocalist|live\s*set|bookings?|mgmt|management|resident|selector|beatmaker)\b", re.I)
MEDIA_WORDS = re.compile(r"\b(?:news|magazine|blog|radio|giornale|quotidiano|webzine|guida|agenda)\b", re.I)


def guess_kind(name: str, bio: str) -> str:
    """Locale, promoter, artista o testata: si capisce dal nome e dalla biografia del profilo."""
    text = plain(f"{name}\n{bio}")
    if MEDIA_WORDS.search(text):
        return "media"
    venue = bool(VENUE_WORDS.search(text)) + 2 * bool(ADDRESS_WORDS.search(text))
    artist = 2 * bool(ARTIST_WORDS.search(text))
    promoter = bool(PROMOTER_WORDS.search(text))
    if venue >= 2 and venue >= artist:
        return "venue"
    if artist and artist >= promoter + venue:
        return "artist"
    if promoter:
        return "promoter"
    return "venue" if venue else "unknown"


class Instagram:
    def __init__(self, city: City):
        self.city = city
        self.cfg = city.settings["instagram"]
        self.user_id = os.environ.get("IG_USER_ID") or str(self.cfg.get("user_id") or "")
        self.token = os.environ.get("IG_ACCESS_TOKEN") or ""
        self.posts_path = city.data / "posts.json"
        self.posts: dict = json.loads(self.posts_path.read_text(encoding="utf-8")) if self.posts_path.exists() else {}
        self.problems: list[str] = []
        self.calls = 0

    @property
    def ready(self) -> bool:
        return bool(self.user_id and self.token)

    def _get(self, username: str, with_media: bool) -> dict:
        fields = PROFILE + (f",media.limit({self.cfg['posts_per_account']}){{{MEDIA}}}" if with_media else "")
        self.calls += 1
        r = requests.get(f"https://graph.facebook.com/{self.cfg['graph_version']}/{self.user_id}",
                         params={"fields": f"business_discovery.username({username}){{{fields}}}", "access_token": self.token},
                         headers=UA, timeout=30)
        if r.status_code != 200:
            err = (r.json().get("error") or {}) if "json" in r.headers.get("content-type", "") else {}
            raise RuntimeError(f"{err.get('code', r.status_code)}: {err.get('message', r.text[:150])}")
        return r.json().get("business_discovery", {})

    def _profile(self, handle: str, bd: dict) -> dict:
        acc = self.city.registry["accounts"].setdefault(handle, {})
        acc.update({"name": clean_name(bd.get("name") or handle), "followers": bd.get("followers_count"), "bio": (bd.get("biography") or "")[:300],
                    "website": bd.get("website"), "checked": now_iso()[:10], "readable": True})
        if not acc.get("kind") or acc.get("kind") == "unknown":
            acc["kind"] = "venue" if self.city.venue_by_handle(handle) else guess_kind(acc["name"], acc["bio"])
        return acc

    def run(self) -> None:
        """Legge un gruppo di profili (i meno recenti per primi) e controlla alcuni profili taggati ancora sconosciuti."""
        if not self.ready:
            self.problems.append("Instagram saltato: manca il segreto IG_ACCESS_TOKEN (o instagram.user_id in city.yaml).")
            return
        accounts = self.city.registry["accounts"]
        today = datetime.now(timezone.utc)
        def due(h):
            a = accounts.get(h, {})
            quiet = a.get("reads", 0) >= 2 and not a.get("events_found") and a.get("kind") != "promoter" and not (self.city.venue_by_handle(h) or {}).get("vote")
            last = a.get("last_read") or ""
            return not (quiet and last and (today - datetime.fromisoformat(last)).days < 30)
        order = [h for h in self.city.watch_list() if due(h)]
        rank = {h: i for i, h in enumerate(order)}
        order.sort(key=lambda h: (accounts.get(h, {}).get("last_read") or "", rank[h]))
        max_age = timedelta(days=self.cfg["max_post_age_days"])
        stop = False
        for h in order[: self.cfg["accounts_per_run"]]:
            try:
                bd = self._get(h, with_media=True)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if re.search(r"\b(?:4|17|32|613)\b:|limit", msg):
                    self.problems.append(f"Instagram: limite orario di Meta raggiunto dopo {self.calls - 1} profili; gli altri al prossimo giro.")
                    stop = True
                    break
                if re.search(r"\b190\b|expired|access token", msg, re.I):
                    self.problems.append("Instagram: il token è scaduto o non valido. Va rinnovato (vedi README, 'Rinnovare il token').")
                    stop = True
                    break
                accounts.setdefault(h, {}).update({"readable": False, "error": msg[:160], "checked": now_iso()[:10]})
                self.problems.append(f"@{h} non leggibile (profilo personale, privato o rinominato).")
                continue
            acc = self._profile(h, bd)
            acc["last_read"] = today.isoformat(timespec="minutes")
            acc["reads"] = acc.get("reads", 0) + 1
            for post in (bd.get("media") or {}).get("data", []):
                at = datetime.fromisoformat(post["timestamp"].replace("+0000", "+00:00"))
                if today - at > max_age or not post.get("caption"):
                    continue
                self.posts[post["id"]] = {"account": h, "at": at.isoformat(timespec="minutes"), "caption": post["caption"], "permalink": post.get("permalink"),
                                          "image": post.get("media_url") or post.get("thumbnail_url")}
        if not stop:
            self._lookups()
            self._hashtags()
        cutoff = (today - max_age - timedelta(days=30)).isoformat()
        self.posts = {k: v for k, v in self.posts.items() if v["at"] >= cutoff}
        self.posts_path.write_text(json.dumps(self.posts, ensure_ascii=False, indent=1), encoding="utf-8")

    def _hashtags(self) -> None:
        """Una volta a settimana: i post più visti con gli hashtag della città (#bergamonightlife...).
        I profili citati nelle didascalie entrano nell'elenco da controllare: così si trovano promoter nuovi."""
        tags = [t.lstrip("#") for t in (self.city.c.get("page") or {}).get("hashtags") or []][:5]
        state = self.city.registry.setdefault("hashtags", {})
        last = state.get("checked")
        if not tags or (last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days < 7):
            return
        state["checked"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
        base = f"https://graph.facebook.com/{self.cfg['graph_version']}"
        found = 0
        for tag in tags:
            try:
                r = requests.get(f"{base}/ig_hashtag_search", params={"user_id": self.user_id, "q": tag, "access_token": self.token}, headers=UA, timeout=30)
                r.raise_for_status()
                hid = r.json()["data"][0]["id"]
                r = requests.get(f"{base}/{hid}/top_media", params={"user_id": self.user_id, "fields": "caption,permalink,timestamp", "limit": 50, "access_token": self.token},
                                 headers=UA, timeout=30)
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                self.problems.append(f"Hashtag #{tag} non letto ({str(e)[:80]}): la scoperta va avanti con locali e tag.")
                return
            for m in r.json().get("data", []):
                for h in set(re.findall(r"@([a-z0-9_.]{3,30})", (m.get("caption") or "").lower())):
                    h = h.strip(".")
                    acc = self.city.registry["accounts"].setdefault(h, {})
                    acc["mentions"] = acc.get("mentions", 0) + 1
                    found += 1
        if found:
            self.problems.append(f"Hashtag: {found} citazioni di profili da controllare.")

    def _lookups(self) -> None:
        """Profili taggati negli eventi e mai visti: nome, follower e biografia dicono chi sono."""
        accounts = self.city.registry["accounts"]
        pending = sorted((h for h, a in accounts.items() if a.get("mentions") and not a.get("checked") and a.get("readable") is not False),
                         key=lambda h: -accounts[h]["mentions"])
        for h in pending[: self.cfg["follower_lookups_per_run"]]:
            try:
                self._profile(h, self._get(h, with_media=False))
            except Exception as e:  # noqa: BLE001
                if re.search(r"\b(?:4|17|32|613)\b:|limit", str(e)):
                    break
                accounts[h].update({"readable": False, "checked": now_iso()[:10]})
