"""Adapters that are designed but not built yet. See ARCHITECTURE.md for the plan for each."""
from .base import NotImplementedSource


class ResidentAdvisorSource(NotImplementedSource):
    type = "resident_advisor"
    note = ("RA has no public API. Plan: query the Milan/Lombardy area listings politely "
            "(daily, cached), map RA artist/club ids to Socials.resident_advisor.")


class InstagramSource(NotImplementedSource):
    type = "instagram"
    note = ("Plan: Instagram Graph API Business Discovery on venue/promoter accounts. Needs an IG "
            "Business/Creator account linked to a Facebook Page and an app token. Captions are "
            "free text, so parsed results go to data/review_queue.yaml, not straight to the calendar.")


class FacebookEventsSource(NotImplementedSource):
    type = "facebook_events"
    note = "Facebook closed third-party access to public events; use manual entries instead."
