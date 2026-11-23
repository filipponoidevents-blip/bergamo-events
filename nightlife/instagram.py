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

from . import ocr
from .city import City, clean_name
from .util import UA, norm, now_iso, plain

log = logging.getLogger("nightlife.instagram")
PROFILE = "username,name,biography,website,followers_count,media_count"
MEDIA = "id,caption,media_type,permalink,timestamp,media_url,thumbnail_url,children{media_url,media_type}"

VENUE_WORDS = re.compile(r"\b(?:club|discoteca|disco\s*(?:bar|pub|club)|lounge|cocktail|bar\b|pub\b|bistrot?|caf[eè]|caffetteria|ristorante|pizzeria|birreria|brewery|live\s*music\s*(?:club|venue)|locale|rooftop|circolo|teatro|spazio|venue|beach|lido|chiosco|osteria|enoteca)", re.I)
ADDRESS_WORDS = re.compile(r"\b(?:via|viale|piazza|piazzale|largo|corso|strada|localit[aà])\s+\w+|📍", re.I)
PROMOTER_WORDS = re.compile(r"\b(?:party|parties|event[is]?|eventi|serat[ae]|format|nights?|crew|collective|collettivo|organizzazion|one\s*night|rave|festival|clubbing|community|label|movement|project|production)", re.I)
ARTIST_WORDS = re.compile(r"\b(?:dj|producer|artist[a]?|musician|musicista|singer|cantante|rapper|vocalist|live\s*set|bookings?|mgmt|management|resident|selector|beatmaker)\b", re.I)
MEDIA_WORDS = re.compile(r"\b(?:news|magazine|blog|radio|giornale|quotidiano|webzine|guida|agenda)\b", re.I)
# L'API restituisce le didascalie senza "@": i profili citati restano parole come "euphoria.clubbing" o "missdivina1".
BARE_HANDLE = re.compile(r"(?<![\w@#/.])([a-z][a-z0-9]*(?:[._][a-z0-9]+)+_?|[a-z][a-z._]{2,}[0-9]{1,6})(?![\w@/]|\.\w)")
CUE_HANDLE = re.compile(r"\b(?:by|con|with|w/|feat\.?|ft\.?|calls|presenta|presents|in collaborazione con|powered by|hosted by|guest)\s+([a-z][a-z0-9._]{5,29})\b")
NOT_HANDLE = re.compile(r"\.(?:it|com|net|org|eu|io|info|html?|php)$|^(?:www|http|e\.g|i\.e|a\.s\.d|s\.r\.l|p\.s)|\d{2}[._]\d{2}|^[a-z]+3$")
CUE_STOP = {"tutti", "tutte", "amici", "amiche", "noi", "voi", "musica", "special", "drink", "ingresso", "tantissimi", "grande", "nostro", "nostra", "questo", "questa",
            "ospiti", "artisti", "partecipazione", "collaborazione", "l'evento", "evento", "serata"}

VENUE_LINE = re.compile(r"^[Q9O0]{1,2}\s*([A-Za-z0-9'&.!À-ÿ ]{2,40}?)\s+-\s+([A-Za-z0-9'&.!À-ÿ ]{3,40}?)(?:\s*[—–_\-+=~.]{2,}.*|\s+\d{1,2}[.:]\d{2}.*|\s+Dalle.*)?$")
TOWN_LINE = re.compile(r"^[A-ZÀ-Ü' ]{3,}\s*\((?:[A-Z]{2})\)+\s*//\s*(.+)$")
GENERIC_PARTY = re.compile(r"^(?:main\s*room|sala\b|side\s*[ab]\b|ticket|halloween|opening|closing|school\s*party|open\s*bar|free\s*dj|happy\s*birthday|back\s*to|"
                           r"dj\s*set|free\s*entry|ingresso|special\s*guest|dalle|lounge\s*bar|live\s*music|sala\s*\d|prevendita|prenota|aperitivo|"
                           r"saturday|friday|sabato|venerd|domenica|serata|party$|latin\s*night|karaoke|cena|dinner|drink|\d)", re.I)


def scout_party_names(text: str) -> list[str]:
    """Dai recap delle pagine scout (testo letto dalle immagini): i nomi delle serate e dei promoter.
    Righe tipo "Q Nikita - Mask Off ---- 22.30-03.30" e "SERIATE (BG) // Hi-Fi Fever, 10€ con drink"."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        m = VENUE_LINE.match(line)
        parts = [m.group(2)] if m else []
        t = TOWN_LINE.match(line)
        if t:
            body = t.group(1)
            by = re.search(r"\bby\s+([A-Za-z0-9._ ]{3,30})", body)
            if by:
                parts.append(by.group(1))
            parts.append(re.split(r"\s+-\s+|[,.:(]|\s+\d", body)[0])
        split = []
        for x in parts:   # "Hidden Hell x Code", "Open All w/Utopia", "After Brunch by The Box": ogni nome per conto suo
            split += re.split(r"\s+x\s+|\s*\bw/\s*|\s+by\s+|\s+feat\.?\s+", x)
        for x in split:
            x = re.sub(r"\s+m+$", "", re.sub(r"\s+", " ", x)).strip(" -—_.#")
            if 3 <= len(x) <= 30 and not GENERIC_PARTY.search(x) and re.search(r"[A-Za-z]{3}", x):
                out.append(x)
    return out


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
            quiet = a.get("reads", 0) >= 2 and not a.get("events_found") and a.get("kind") not in ("promoter", "scout") and not (self.city.venue_by_handle(h) or {}).get("vote")
            last = a.get("last_read") or ""
            return not (quiet and last and (today - datetime.fromisoformat(last)).days < 30)
        order = [h for h in self.city.watch_list() if due(h)]
        rank = {h: i for i, h in enumerate(order)}
        order.sort(key=lambda h: (accounts.get(h, {}).get("kind") != "scout", accounts.get(h, {}).get("last_read") or "", rank[h]))   # le pagine scout a ogni giro
        max_age = timedelta(days=self.cfg["max_post_age_days"])
        stop = False
        if not order:
            self.problems.append("Instagram: ancora nessun profilo da leggere (si aggiungono man mano che i locali vengono trovati).")
        for h in order[: self.cfg["accounts_per_run"]]:
            try:
                bd = self._get(h, with_media=True)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if re.search(r"\b(?:4|17|32|613)\b:|limit", msg):
                    self.problems.append(f"Instagram: limite orario di Meta raggiunto dopo {self.calls - 1} profili; gli altri al prossimo giro. ({msg[:100]})")
                    stop = True
                    break
                if re.search(r"log in to www\.facebook\.com|checkpoint", msg, re.I):
                    self.problems.append("Instagram: Meta ha bloccato l'accesso finché non entri su facebook.com con l'account collegato e segui le istruzioni (controllo di sicurezza). Poi, se serve, rinnova il token.")
                    stop = True
                    break
                if re.search(r"\b190\b|expired|access token", msg, re.I):
                    self.problems.append(f"Instagram: il token è scaduto o non valido. Va rinnovato (vedi README, 'Rinnovare il token'). Errore Meta: {msg[:160]}")
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
                old = self.posts.get(post["id"], {})
                self.posts[post["id"]] = {"account": h, "at": at.isoformat(timespec="minutes"), "caption": post["caption"], "permalink": post.get("permalink"),
                                          "image": post.get("media_url") or post.get("thumbnail_url"),
                                          "slides": [c["media_url"] for c in (post.get("children") or {}).get("data", []) if c.get("media_type") == "IMAGE" and c.get("media_url")][:10],
                                          **({"ocr": old["ocr"]} if "ocr" in old else {})}
        self._ocr_scouts()
        self._candidates()
        if not stop:
            self._lookups()
            self._guess_venues()
            self._city_pages()
            self._guess_parties()
            if self.cfg.get("hashtags"):   # spento di base: in zona gli hashtag si usano poco e Meta non dà il permesso
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
        pending = sorted((h for h, a in accounts.items() if (a.get("mentions") or a.get("cands")) and not a.get("checked") and a.get("readable") is not False),
                         key=lambda h: -(2 * (accounts[h].get("mentions") or 0) + (accounts[h].get("cands") or 0)))
        budget = max(self.cfg["follower_lookups_per_run"], self.cfg["accounts_per_run"] + 2 - self.calls - 4)
        for h in pending[:budget]:
            try:
                acc = self._profile(h, self._get(h, with_media=False))
                if acc.get("kind") == "promoter" and (acc.get("via_scout") or (acc.get("cands") or 0) + (acc.get("mentions") or 0) >= self.city.settings["instagram"]["auto_watch_after"]):
                    acc["watch"] = True
            except Exception as e:  # noqa: BLE001
                if re.search(r"\b(?:4|17|32|613)\b:|limit", str(e)):
                    break
                accounts[h].update({"readable": False, "checked": now_iso()[:10]})


    def _candidates(self) -> None:
        """Nelle didascalie i profili citati compaiono senza "@": li riconosce dalla forma (punti, trattini bassi, numeri)
        o dalla parola prima (by, con, feat, presenta...). Poi _lookups li controlla uno per uno."""
        accounts = self.city.registry["accounts"]
        count: dict[str, int] = {}
        via_scout: set[str] = set()
        for post in self.posts.values():
            text = plain(post.get("caption") or "")
            low = text.lower()
            seen = {m.lower().rstrip(".") for m in re.findall(r"@([A-Za-z0-9._]{3,30})", plain(post.get("ocr") or ""))}   # nelle immagini la @ resta
            for m in BARE_HANDLE.finditer(text):   # solo minuscole: un handle si scrive così
                seen.add(m.group(1))
            for m in CUE_HANDLE.finditer(low):
                h = m.group(1).rstrip("._")
                if h not in CUE_STOP and h in text and (len(h) >= 10 or re.search(r"[._0-9]", h)):   # minuscolo nel testo, non una parola qualsiasi
                    seen.add(h)
            for h in seen:
                h = h.rstrip("._")
                if len(h) < 4 or NOT_HANDLE.search(h) or h == post.get("account"):
                    continue
                count[h] = count.get(h, 0) + 1
                if accounts.get(post.get("account"), {}).get("kind") == "scout":
                    via_scout.add(h)
        for h, n in count.items():
            accounts.setdefault(h, {})["cands"] = n
            if h in via_scout:
                accounts[h]["via_scout"] = True

    def _guess_venues(self, max_calls: int = 6) -> None:
        """Locali trovati sulla mappa ma senza Instagram: prova i nomi utente più probabili (nikitaclub, nikita.bergamo...)
        e tiene il profilo solo se nome o biografia parlano di quel locale e della zona."""
        accounts = self.city.registry["accounts"]
        today = now_iso()[:10]
        venues = [v for v in self.city.registry["venues"].values()
                  if not v.get("instagram") and not v.get("removed") and not v.get("ig_guessed") and v.get("type") in ("club", "live_club", "open_air", "arena")]
        venues.sort(key=lambda v: (v.get("type") != "club", -(v.get("auto_vote") or 0)))
        city_words = {norm(self.city.c["name"]), "bg", "bergamo"}
        calls = 0
        for v in venues:
            words = [w for w in norm(v["name"]).split() if w not in ("club", "disco", "discoteca", "the", "il", "la", "lo", "le")]
            if not words or calls >= max_calls:
                continue
            slug = "".join(words)
            tries = [slug + "club", slug, slug + "bergamo", slug + ".club", slug + "_club"] if "club" not in norm(v["name"]) else [slug + "club", slug, slug + "bergamo"]
            towns = {w for w in norm(v.get("address") or "").split() if len(w) > 3 and not w.isdigit()} - {"via", "viale", "piazza", "strada"}
            v["ig_guessed"] = today
            for h in tries:
                if calls >= max_calls or h in accounts:
                    continue
                calls += 1
                try:
                    bd = self._get(h, with_media=False)
                except Exception as e:  # noqa: BLE001
                    if re.search(r"\b(?:4|17|32|613)\b:|limit", str(e)):
                        return
                    continue
                text = norm(f"{bd.get('username')} {bd.get('name')} {bd.get('biography')} {bd.get('website')}")
                if all(w in text for w in words) and (city_words | towns) & set(text.split()):
                    acc = self._profile(h, bd)
                    acc["kind"] = "venue"
                    v["instagram"] = h
                    self.problems.append(f"Trovato Instagram di {v['name']}: @{h}.")
                    break

    def _city_pages(self, max_calls: int = 6) -> None:
        """Pagine che raccontano la nightlife della città (es. @nightlifebergamo): lì si incrociano tanti promoter.
        Prova i nomi tipici, tiene solo quelle che parlano della città e di serate. Si leggono SOLO per scoprire
        promoter e locali (tipo "scout"): i loro post non entrano mai nel calendario."""
        accounts = self.city.registry["accounts"]
        state = self.city.registry.setdefault("city_pages", {"tried": []})
        slug = norm(self.city.c["name"]).replace(" ", "")
        own = ((self.city.c.get("page") or {}).get("handle") or "").lower().lstrip("@")
        names = [f"nightlife{slug}", f"{slug}nightlife", f"nightlife_{slug}", f"nightlife.{slug}", f"serate{slug}", f"{slug}serate",
                 f"{slug}bynight", f"{slug}_by_night", f"movida{slug}", f"{slug}movida", f"eventi{slug}", f"{slug}eventi",
                 f"{slug}night", f"{slug}clubbing", f"clubbing{slug}", f"party{slug}", f"{slug}party", f"discoteche{slug}", f"{slug}discoteche"]
        calls = 0
        for h in names:
            if calls >= max_calls:
                break
            if h in state["tried"] or h == own:
                continue
            state["tried"].append(h)
            if accounts.get(h, {}).get("checked"):
                bd = {"username": h, "name": accounts[h].get("name"), "biography": accounts[h].get("bio")}
            else:
                calls += 1
                try:
                    bd = self._get(h, with_media=False)
                except Exception as e:  # noqa: BLE001
                    if re.search(r"\b(?:4|17|32|613)\b:|limit", str(e)):
                        state["tried"].remove(h)
                        return
                    continue
            text = norm(f"{bd.get('username')} {bd.get('name')} {bd.get('biography')}")
            if slug in text.replace(" ", "") and re.search(r"\b(?:nightlife|night|serate|serata|eventi|party|club|clubbing|movida|discoteche|disco)\b", text):
                acc = self._profile(h, bd) if "followers_count" in bd else accounts[h]
                acc.update({"kind": "scout", "watch": True})
                self.problems.append(f"Pagina nightlife della città trovata: @{h} (letta solo per scoprire promoter e locali).")

    def _ocr_scouts(self) -> None:
        """Pagine scout (es. @nightlifebergamo): il recap delle serate è nelle immagini del carosello, non nella didascalia.
        Si legge il testo di tutte le slide, una volta per post. Solo per scoprire profili: niente entra nel calendario."""
        if not ocr.AVAILABLE:
            if any(self.city.registry["accounts"].get(p["account"], {}).get("kind") == "scout" for p in self.posts.values()):
                self.problems.append("Lettura immagini saltata: Tesseract non installato.")
            return
        scouts = {h for h, a in self.city.registry["accounts"].items() if a.get("kind") == "scout"}
        done = 0
        for p in sorted(self.posts.values(), key=lambda p: p["at"], reverse=True):
            urls = p.get("slides") or [p.get("image")]
            if p["account"] not in scouts or done >= 15 or ("ocr" in p and p.get("ocr_n", 1) >= len(urls)):
                continue
            texts = [t for u in urls if u and (t := ocr.read_image(u))]
            p["ocr"] = "\n\n".join(texts)
            p["ocr_n"] = len(urls)
            done += 1
        if done:
            self.problems.append(f"Lette le immagini di {done} post delle pagine scout.")

    def _guess_parties(self, max_calls: int = 12) -> None:
        """Nomi di serate e promoter letti nei recap delle pagine scout (es. "Hidden Hell", "Hi-Fi Fever"):
        prova i nomi utente più probabili e tiene il profilo solo se il nome corrisponde e parla di serate o della zona.
        Il profilo trovato viene seguito come promoter."""
        accounts = self.city.registry["accounts"]
        scouts = {h for h, a in accounts.items() if a.get("kind") == "scout"}
        state = self.city.registry.setdefault("parties", {})
        counts: dict[str, list] = {}
        for p in self.posts.values():
            if p["account"] in scouts:
                for n in scout_party_names(p.get("ocr") or ""):
                    counts.setdefault(norm(n), [n, 0])[1] += 1
        for k, (n, c) in counts.items():
            st = state.setdefault(k, {"name": n, "tried": []})
            st["count"] = c
        slug_city = norm(self.city.c["name"]).replace(" ", "")
        calls = 0
        for k, st in sorted(state.items(), key=lambda kv: -kv[1].get("count", 0)):
            if st.get("done"):
                continue
            slug = re.sub(r"[^a-z0-9]", "", plain(st["name"]).lower())
            known = next((h for h, a in accounts.items() if a.get("kind") in ("promoter", "venue") and len(slug) >= 4 and
                          (norm(a.get("name") or "").replace(" ", "") == slug or norm(a.get("party_name") or "").replace(" ", "") == slug or h.startswith(slug))), None)
            if known:   # già conosciuto (es. Hidden Hell = @hiddenhell.techno)
                st.update({"handle": known, "done": True})
                continue
            if len(slug) < 4:
                st["done"] = True
                continue
            if calls >= max_calls:
                continue
            raw = plain(st["name"]).lower().replace(" ", "")
            tries = ([raw] if re.fullmatch(r"[a-z0-9._]{4,30}", raw) and raw != slug else []) + [slug, slug + "party", slug + slug_city, slug + "bg", slug + "_official"]
            for h in tries:
                if calls >= max_calls:
                    break
                if h in st["tried"]:
                    continue
                st["tried"].append(h)
                a = accounts.get(h, {})
                fetched = not a.get("checked")
                if not fetched:
                    bd = {"username": h, "name": a.get("name"), "biography": a.get("bio"), "followers_count": a.get("followers")}
                else:
                    calls += 1
                    try:
                        bd = self._get(h, with_media=False)
                    except Exception as e:  # noqa: BLE001
                        if re.search(r"\b(?:4|17|32|613)\b:|limit", str(e)):
                            st["tried"].remove(h)
                            return
                        continue
                who = norm(f"{bd.get('username')} {bd.get('name')}").replace(" ", "")
                about = norm(f"{bd.get('name')} {bd.get('biography')}")
                if slug in who and (bd.get("followers_count") or 0) >= 200 and \
                        re.search(rf"\b(?:{slug_city}|bg|party|events?|eventi|serat\w*|club\w*|night|crew|dj|format|collective|rave)\b", about):
                    acc = self._profile(h, bd) if fetched else accounts[h]
                    if acc.get("kind") in (None, "unknown", "artist"):
                        acc["kind"] = "promoter"
                    if acc.get("kind") == "promoter":
                        acc["watch"] = True
                        acc["party_name"] = st["name"]
                    st["handle"] = h
                    st["done"] = True
                    self.problems.append(f"Serata \"{st['name']}\" → @{h}.")
                    break
            else:
                st["done"] = True
