"""Airline vocabulary for warm-start steering. Matching lives in `sectors.steering`."""

from __future__ import annotations

from sectors.steering import CorpusSteering, Vocabulary, scrub_text, steer
from sectors.airline.pack import EVENT_NAMESPACE

__all__ = ["CorpusSteering", "VOCABULARY", "scrub_text", "steering_from_text"]

VOCABULARY = Vocabulary(
    channels=(
        ("call centre", "call_centre"),
        ("call center", "call_centre"),
        ("mobile app", "app"),
        ("app", "app"),
        ("airport", "airport"),
        ("kiosk", "airport"),
        ("web", "web"),
        ("online", "web"),
    ),
    products=(
        ("premium economy", "premium_economy"),
        ("business class", "business"),
        ("business", "business"),
        ("economy", "economy"),
    ),
)


def steering_from_text(text: str) -> CorpusSteering:
    return steer(text, VOCABULARY, EVENT_NAMESPACE)
