"""Pull safe steering terms out of warm-start text.

The extractor keeps a small banking vocabulary. It does not copy names,
addresses, account numbers, or provider keys into a trajectory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    ("mobile", "mobile"),
    ("branch", "branch"),
    ("web", "web"),
)
_PRODUCTS = (
    ("current account", "current"),
    ("checking", "current"),
    ("savings", "savings"),
    ("mortgage", "mortgage"),
    ("loan", "loan"),
    ("complaint", "complaint"),
    ("card", "card"),
)


@dataclass(frozen=True)
class CorpusSteering:
    currency: str | None
    channel: str | None
    products: tuple[str, ...]
    terms: tuple[str, ...]


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
    return CorpusSteering(currency=currency, channel=channel, products=tuple(products), terms=terms)
