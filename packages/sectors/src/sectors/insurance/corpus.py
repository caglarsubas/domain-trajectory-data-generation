"""Pull safe steering terms out of warm-start insurance text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sectors.insurance.pack import EVENT_NAMESPACE

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SECRET = re.compile(r"\b(?:sk|xai|AIza)[A-Za-z0-9_\-]{6,}\b")
_LONG_NUMBER = re.compile(r"\d{6,}")

_CURRENCIES = (
    ("gbp", "GBP"),
    ("usd", "USD"),
    ("eur", "EUR"),
    ("try", "TRY"),
    ("sterling", "GBP"),
    ("dollar", "USD"),
    ("euro", "EUR"),
    ("lira", "TRY"),
)
_CHANNELS = (
    ("call centre", "call_centre"),
    ("call center", "call_centre"),
    ("broker", "broker"),
    ("mobile", "mobile"),
    ("branch", "branch"),
    ("web", "web"),
)
_PRODUCTS = (
    ("motor", "motor"),
    ("car insurance", "motor"),
    ("home", "home"),
    ("household", "home"),
    ("travel", "travel"),
    ("life", "life"),
    ("health", "health"),
    ("pet", "pet"),
)


@dataclass(frozen=True)
class CorpusSteering:
    currency: str | None
    channel: str | None
    products: tuple[str, ...]
    terms: tuple[str, ...]
    events: tuple[str, ...] = ()


def scrub_text(text: str) -> str:
    cleaned = text.replace("\x00", " ")
    cleaned = _EMAIL.sub(" ", cleaned)
    cleaned = _SECRET.sub(" ", cleaned)
    cleaned = _LONG_NUMBER.sub(" ", cleaned)
    return cleaned


def steering_from_text(text: str) -> CorpusSteering:
    folded = scrub_text(text).lower()
    currency = next((code for token, code in _CURRENCIES if token in folded), None)
    channel = next((name for token, name in _CHANNELS if token in folded), None)
    products: list[str] = []
    for token, name in _PRODUCTS:
        if token in folded and name not in products:
            products.append(name)
    terms = tuple(dict.fromkeys(item for item in (currency, channel, *products) if item))
    return CorpusSteering(
        currency=currency,
        channel=channel,
        products=tuple(products),
        terms=terms,
        events=_events(folded),
    )


def _events(folded: str) -> tuple[str, ...]:
    found: list[str] = []
    for name in EVENT_NAMESPACE:
        if name in folded or name.replace(".", " ") in folded:
            found.append(name)
    return tuple(found)
