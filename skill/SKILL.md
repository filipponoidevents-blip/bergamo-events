---
name: nightlife-calendar-city
description: Set up or rerun the automatic nightlife calendar (serata/preserata, ranking, promoter calendar, infographic data) for Bergamo or a new Italian city, using the nightlife tracker repo.
---

# Nightlife calendar for a city

Repo: `filipponoidevents-blip/bergamo-events` (private). Program: `nightlife/` (Python, free, no paid AI).
Rules: `REGOLE.md` in the repo. Read it before changing behaviour; keep it the single place where rules are written.
Talk to Filippo in Italian, simple words, click-by-click steps, free tools only.

## What the system does (no human input needed)
1. Discovery: OpenStreetMap venues in the radius (nightclub, music_venue, dance, bars with live music), venue websites → Instagram handles,
   Xceed club pages, newspaper agendas, and Instagram chain discovery (tagged profiles are checked: name, followers, bio → venue/promoter/artist;
   promoters tagged in ≥2 events are followed automatically).
2. Collection: Instagram Business Discovery (≈20 profiles per run, every 2 h), Xceed, DICE pages, schema.org JSON-LD pages, articles.
3. Reading: free rule-based reader (`nightlife/reader.py`): dates (also monthly programmes), times, rooms, price, promoter, guests, genre, type, confidence.
4. Merge across sources, serata/preserata, score 0-100, competition per night.
5. Outputs in `cities/<city>/out/` and branch `feed-v2`: .ics calendars (serate, preserate, grandi eventi, tutto), events CSV, promoter calendar (md/json),
   infographics JSON (week/month, serate by genre family, preserate, big events, caption @handles), venue and profile lists, status report.
   Optional `old_feeds` in city.yaml publishes extra .ics files with legacy names (Bergamo: bergamo.ics, alerts.ics, all.ics, other.ics).
6. Post graphics (`python -m nightlife posts`): wall-calendar slides, IG 4:5 and TikTok 9:16, Monday week / Wednesday "Prima della serata" /
   last Sunday next month; uploaded as the run's "post" artifact.

## New city: steps
1. `python -m nightlife new-city <nome> <lat> <lon> [raggio]` → creates `cities/<nome>/city.yaml` and an empty `conoscenze.yaml`.
2. Fill in `city.yaml`: `big_event_provinces` (neighbouring province codes), `page` (brand, handle, hashtags without spaces),
   `instagram.user_id` (the Business account that reads; the same token can serve several cities), local agenda pages under `sources.jsonld.pages`
   and `sources.articles.pages` if a local newspaper has them (check with a probe run on GitHub: the cloud sandbox cannot reach most sites).
3. Optional `conoscenze.yaml`: `seeds` (2-5 main club Instagram handles speed up discovery). Everything else is optional; the person's choices always win.
4. Commit on a branch and push: the `nightlife-v2` workflow runs on push, then every 2 h once on the default branch (`python -m nightlife run --city all`).
5. First week: discovery grows by itself (Meta limit ≈20 profiles/run). Check `cities/<nome>/out/<nome>_stato.md` and `<nome>_locali.csv` after a day.
6. Phone calendars: the person subscribes once to the `.ics` URLs served from `feed-v2` (Cloudflare Pages pointed at that branch, no build command).

## Rerun / check Bergamo
- Status: `cities/bergamo/out/bergamo_stato.md` (sources skipped, Instagram limit or expired token, events to check).
- Offline reread after reader changes: `python -m nightlife run --city bergamo --offline`.
- Bergamo's hand-made v1 knowledge lives in `cities/bergamo/conoscenze-importate.yaml` (regenerate with `python -m nightlife import-v1 archivio/database-v1 --city bergamo`); corrections go in `conoscenze.yaml`.
- Coverage vs the old hand-made list (autonomous discovery only, knowledge ignored): `python -m nightlife compare archivio/database-v1 --city bergamo` (also printed in every workflow run).

## Things to know
- Instagram token lasts ~60 days: a reminder appears in the calendar 10 days before; renewal steps in README. Update `token_renewed` after.
- If Meta answers "You cannot access the app till you log in to www.facebook.com", the person must log in to facebook.com with the linked account and pass the security check; renew the token if it still fails.
- Google Maps (Places API New) fills the clubs OpenStreetMap misses: optional secret `GOOGLE_PLACES_KEY` (Google Cloud project, billing linked, Places API (New) enabled, key restricted to it, daily quota capped at 100). ~20 requests/month, inside the free tier.
- Only public Business/Creator Instagram accounts are readable; personal accounts are marked unreadable and skipped.
- Blocked/unreadable from GitHub (Nov 2026): TicketSMS, Upass, Eventbrite (405), Fourvenues (403), Resident Advisor (403), Shotgun (429). Xceed club pages and DICE city pages work.
- The sandbox where Claude runs has no internet for these sites: test sources by pushing and reading the workflow logs.
- Never commit secrets. Data folders are written by the bot; don't hand-edit `data/`.
- Agreed conventions (Filippo): calendar title `[Genre] Venue · Party w/ Headliner`; graphics line 1 "Party @ Venue", line 2 "genre, headliner, room"; profile names in graphics, @handles only in captions; phone calendar start time only, after-midnight starts at 23:59 the evening before; post pages by family House/Elettronica, Hip Hop/Trap, Commerciale, Altro; preserate separate.
