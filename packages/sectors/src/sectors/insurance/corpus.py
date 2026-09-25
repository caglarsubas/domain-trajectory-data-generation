"""Insurance vocabulary for warm-start steering. Matching lives in `sectors.steering`."""

from __future__ import annotations

from sectors.insurance.pack import EVENT_NAMESPACE
from sectors.steering import CorpusSteering, Vocabulary, scrub_text, steer

__all__ = ["CorpusSteering", "VOCABULARY", "scrub_text", "steering_from_text"]

VOCABULARY = Vocabulary(
    channels=(
        ("call centre", "call_centre"),
        ("call center", "call_centre"),
        ("broker", "broker"),
        ("mobile", "mobile"),
        ("branch", "branch"),
        ("web", "web"),
        ("online", "web"),
    ),
    products=(
        ("motor", "motor"),
        ("car insurance", "motor"),
        ("home", "home"),
        ("household", "home"),
        ("travel", "travel"),
        ("life", "life"),
        ("health", "health"),
        ("pet", "pet"),
    ),
)


def steering_from_text(text: str) -> CorpusSteering:
    return steer(text, VOCABULARY, EVENT_NAMESPACE)
