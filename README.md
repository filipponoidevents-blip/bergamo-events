# Bergamo Events Tracker

Collects dance-music events in Bergamo +20 km, flags big announcements in nearby provinces,
and publishes them as subscribable calendar feeds. Also renders weekly/monthly carousel slides
and prepares (but does not send) Instagram/TikTok post bundles with tags.

```
pip install -e .            # or: PYTHONPATH=src python -m bget ...
python -m bget run          # collect + score + write calendars + alerts
python -m bget carousel --week 2026-11-20
python -m bget carousel --month 2026-11
python -m pytest
```

The data shipped in `data/manual_events.yaml` and `tests/fixtures/` is **sample data**
(titles start with `[SAMPLE]`). Venue coordinates in `config/venues.yaml` are approximate
and social handles are blank until checked by hand.

## More cities

Bergamo lives at the repo root (`config/`, `data/`, `out/`). Every other city has the same folders in
`cities/<name>/` (Brescia: `cities/brescia/`). A city's `settings.yaml` only holds what differs (centre,
radius, alert provinces, labels); the rest is inherited, and `watchlist.yaml` is shared.
`python -m bget --city brescia run`, or `--city all` (what the workflow runs). Feeds are published next to
Bergamo's as `brescia.ics`, `brescia_alerts.ics`, `brescia_all.ics`; reports go in `brescia/` on the site.
To add a city: `docs/nuova-citta.md`.

## How it fits together

```
config/sources.yaml ──► sources/* adapters ──► enrich venue (config/venues.yaml)
                                                   │
                                                   ▼
                       data/events.json ◄── Store (merge same venue + same night)
                                                   │
                                     relevance.score_event (settings + watchlist)
                                                   │
                 ┌────────────────┬────────────────┼──────────────────┐
                 ▼                ▼                ▼                  ▼
          out/bergamo.ics   out/alerts.ics   out/alerts.md   carousel.py ─► social.py
            (LOCAL)            (ALERT)        (big news)      PNG slides    post bundle JSON
```

### Data model (`src/bget/models.py`)
- **Event**: title, start/end (Europe/Rome), venue, artists, genres, category
  (club / festival / concert / party), event + ticket URLs, image, price, promoter,
  which sources reported it, and the scoring result (score, scope, distance, reasons, alert level).
- **Venue**: name, city, province, coordinates, kind (club / live_club / open_air / arena), capacity, socials.
- **Artist**: name, role, genres, socials.
- **Main genre** of each night (src/bget/genres.py): a hand-set genre in `config/genres.yaml` (per night, venue or promoter) wins; then genre words in the post; then the promoter's, then the venue's usual genre.
- **Socials**: Instagram, TikTok, Facebook, website, Resident Advisor id. These are the tags
  used in Phase 3; `Event.all_handles("instagram")` returns venue, promoter, artist handles in order.
- **Night**: a 01:00 start counts as the previous evening, so the same party reported by the
  venue (23:00) and a ticketing site (00:30) merges into one event.

### Sources (`config/sources.yaml`)
| Adapter | Status | Covers |
|---|---|---|
| `manual` | built | Anything seen only on Instagram/Facebook; also the review queue |
| `jsonld` | built | Any page with schema.org Event JSON-LD: Dice, Xceed, Shotgun, Eventbrite, Ticketone, many venue sites |
| `ical` | built | Venues/promoters that publish a public .ics or Google Calendar |
| `resident_advisor` | planned | RA listings for the Milan/Lombardy area. No public API, so polite daily queries + cache; check RA's terms |
| `instagram` | built, needs credentials | Reads recent posts of watched public Business/Creator accounts (Graph API *Business Discovery*); Claude reads caption + flyer and extracts events. See below |
| `facebook_events` | not possible | Facebook closed third-party access to public events; use `manual` |

### Relevance rules (`config/settings.yaml`, `config/watchlist.yaml`)
- **Genre score**: best matching genre weight (techno / tech house 3, house 2.5, … commerciale 0.3),
  plus a bonus for club or open-air venues.
- **Watchlist**: DJ headliners (+4), crossover live acts (+4), national brands/festivals (+5),
  festival category (+2), venue capacity ≥ 5000 (+1.5).
- **LOCAL** (main calendar): within 20 km and score ≥ 2.
- **ALERT** (alerts calendar): outside 20 km, in MI/MB/BS/LC/CO/CR/VA/LO/SO, and either score ≥ 6 or a
  watchlist hit. National brands alert from anywhere in Italy.
- **Concerts** are dropped unless the act is on the crossover list.
- Scores are recomputed on every run, so editing the YAML re-filters stored events.

### Instagram auto-check (`src/bget/sources/instagram*.py`)
1. Each run reads the latest posts of the accounts listed under `instagram_watch` in `config/sources.yaml`.
2. New posts (not older than 21 days) are read by the free pattern matcher (default; no key) or, with
   `reader: claude` in settings, by Claude (caption, plus flyers if `read_images: true`); each returns structured
   events (date, times, venue, line-up with tagged handles, genres, ticket link) and a confidence score.
3. Confidence ≥ 0.85: straight onto the calendar. Lower: waits in the review queue.
   The queue is published as `review.md` next to the calendars.
4. Approve or reject by id in `config/ig_decisions.yaml` (edit it on GitHub). Rejecting removes the event.
Secrets needed in GitHub Actions: `IG_USER_ID`, `IG_ACCESS_TOKEN` (+ `ANTHROPIC_API_KEY` only for `reader: claude`).
Free flyer reading: accounts marked `ocr: true` get their images read with Tesseract OCR (installed in the
workflow) and the text is added to the caption. Accounts marked `listing: true` (agenda pages) are read as a weekly agenda: each day heading + known venue line becomes an event, sent to review.
Limits: only public Business/Creator accounts; Stories are not readable by the API (use `manual`).

### Calendar output
Three feeds: `bergamo.ics` (local), `alerts.ics` (⚡ big events), `all.ics` (both).
`.github/workflows/update.yml` runs the collector every 6 hours (and on config changes), commits
`data/events.json`, and force-pushes only the feeds to the `feed` branch. Cloudflare Pages serves that
branch (no build command), so the repo stays private while the calendars get stable URLs like
`https://<project>.pages.dev/bergamo.ics` to subscribe to once in Google/Apple Calendar.

### Serata or preserata (`src/bget/fascia.py`)
Every night gets a *fascia*: serata (starts 23:30 or later and ends 03:00 or later) or preserata.
Captions rarely give an end time, so the caption reader (`instagram_rules.py`) also picks up the end
time in all its usual forms ("dalle ore 23:30 alle 06:00", "Chiusura ore 04:00", "h. 23 – 03",
"till late"), price clues (list / tickets = serata, free entry = preserata) and words like "aperitivo".
When those don't decide, the venue's `fascia:` in `config/venues.yaml` does. Fix a single night with
`fascia: {id: preserata}` in `config/ig_decisions.yaml`. Each run writes `out/serata_preserata.md` with
every upcoming night, its times and the reason for its fascia. Stored captions are re-read on every run,
so reader improvements also fix nights read earlier.

### Phase 2: carousels (`src/bget/carousel.py`)
1080×1350 PNG slides. Weekly: cover, "In zona" (up to 5 events per slide), "Vale il viaggio" (alerts).
Monthly: cover, one or more slides per week, alerts. The design is a plain placeholder; colours, fonts
and handle live in `THEME`.

### Phase 3: auto-posting (`src/bget/social.py`)
`carousel` already writes `*_post.json` with the caption, @mentions, per-slide Instagram `user_tags`,
and a list of venues/artists still missing a handle. A publisher that sends it is not built. What it needs:
- **Instagram**: a Business or Creator account and a Meta developer app with content-publishing permission.
  Images must be at a public URL (publish them alongside the feeds). Carousels: up to 10 items via the API.
- **TikTok**: Content Posting API (photo mode), a TikTok developer app, and passing TikTok's app audit.
  Until audited, posts can only be private. Tags are @mentions in the caption.
- Posting should stay behind a manual "approve" step at first.

## Next steps
1. Verify the seed venues and fill in real Instagram/TikTok handles.
2. Add real source URLs (venue event pages, Dice/Xceed pages) and enable them.
3. Build the Resident Advisor adapter.
4. Connect Cloudflare Pages to the `feed` branch so the calendar URLs go live.
