"""Hotel vocabulary for warm-start steering. Matching lives in `sectors.steering`."""

from __future__ import annotations

from sectors.steering import CorpusSteering, Vocabulary, scrub_text, steer
from sectors.hotel.pack import EVENT_NAMESPACE

__all__ = ["CorpusSteering", "VOCABULARY", "scrub_text", "steering_from_text"]

VOCABULARY = Vocabulary(
    channels=(
        ("call centre", "call_centre"),
        ("call center", "call_centre"),
        ("front desk", "front_desk"),
        ("reception", "front_desk"),
        ("mobile app", "app"),
        ("app", "app"),
        ("web", "web"),
        ("online", "web"),
    ),
    products=(
        ("non-refundable", "non_refundable"),
        ("non refundable", "non_refundable"),
        ("all inclusive", "all_inclusive"),
        ("all-inclusive", "all_inclusive"),
        ("bed and breakfast", "bed_and_breakfast"),
        ("breakfast", "bed_and_breakfast"),
        ("flexible", "flexible"),
    ),
)


def steering_from_text(text: str) -> CorpusSteering:
    return steer(text, VOCABULARY, EVENT_NAMESPACE)
