"""Banking vocabulary for warm-start steering. Matching lives in `sectors.steering`."""

from __future__ import annotations

from sectors.banking.pack import EVENT_NAMESPACE
from sectors.steering import CorpusSteering, Vocabulary, scrub_text, steer

__all__ = ["CorpusSteering", "VOCABULARY", "scrub_text", "steering_from_text"]

VOCABULARY = Vocabulary(
    channels=(
        ("call centre", "call_centre"),
        ("call center", "call_centre"),
        ("mobile", "mobile"),
        ("branch", "branch"),
        ("web", "web"),
        ("online banking", "web"),
        ("internet banking", "web"),
    ),
    products=(
        ("current account", "current"),
        ("checking", "current"),
        ("savings", "savings"),
        ("mortgage", "mortgage"),
        ("loan", "loan"),
        ("complaint", "complaint"),
        ("card", "card"),
    ),
)


def steering_from_text(text: str) -> CorpusSteering:
    return steer(text, VOCABULARY, EVENT_NAMESPACE)
