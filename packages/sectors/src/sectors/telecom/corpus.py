"""Telecom vocabulary for warm-start steering. Matching lives in `sectors.steering`."""

from __future__ import annotations

from sectors.steering import CorpusSteering, Vocabulary, scrub_text, steer
from sectors.telecom.pack import EVENT_NAMESPACE

__all__ = ["CorpusSteering", "VOCABULARY", "scrub_text", "steering_from_text"]

VOCABULARY = Vocabulary(
    channels=(
        ("call centre", "call_centre"),
        ("call center", "call_centre"),
        ("mobile app", "app"),
        ("app", "app"),
        ("store", "store"),
        ("shop", "store"),
        ("web", "web"),
        ("online", "web"),
    ),
    products=(
        ("pay monthly", "mobile_postpaid"),
        ("postpaid", "mobile_postpaid"),
        ("contract", "mobile_postpaid"),
        ("prepaid", "mobile_prepaid"),
        ("pay as you go", "mobile_prepaid"),
        ("sim only", "sim_only"),
        ("sim-only", "sim_only"),
        ("broadband", "broadband"),
        ("fibre", "fibre"),
        ("fiber", "fibre"),
    ),
)


def steering_from_text(text: str) -> CorpusSteering:
    return steer(text, VOCABULARY, EVENT_NAMESPACE)
