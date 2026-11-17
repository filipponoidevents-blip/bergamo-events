from .base import Source
from .ical import ICalSource
from .jsonld import JsonLdSource
from .manual import ManualSource
from .instagram import InstagramSource
from .press import PressSource
from .planned import FacebookEventsSource, ResidentAdvisorSource

REGISTRY: dict[str, type[Source]] = {
    cls.type: cls
    for cls in (ManualSource, JsonLdSource, ICalSource,
                ResidentAdvisorSource, InstagramSource, FacebookEventsSource, PressSource)
}

__all__ = ["REGISTRY", "Source"]
