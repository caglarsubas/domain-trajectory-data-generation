"""Constrained semi-Markov generator for hotel guest journeys.

The hotel state machines in `sectors.hotel.spec` decide which events are legal; the shared
lifecycle engine samples among them and times each step. An alternative path is a simulated branch,
not a causal counterfactual. The generator does not call a provider.
"""

from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.journeys import STUDIO_TRAJECTORY_CAP, Note, generate_bundle
from sectors.hotel.corpus import CorpusSteering, steering_from_text
from sectors.hotel.spec import GENERATOR_ID, PACK

__all__ = ["GENERATOR_ID", "STUDIO_TRAJECTORY_CAP", "Note", "generate_hotel_bundle"]


def generate_hotel_bundle(**kwargs) -> TrajectoryBundle:
    return generate_bundle(PACK, steering_from_text, CorpusSteering(None, None, (), ()), **kwargs)
