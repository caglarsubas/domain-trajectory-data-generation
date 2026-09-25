"""Constrained semi-Markov generator for retail banking journeys.

The banking state machines in `sectors.banking.spec` decide which events are
legal; the shared lifecycle engine samples among them and times each step. An
alternative path is a simulated branch, not a causal counterfactual. The
generator does not call a provider.
"""

from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.banking.corpus import CorpusSteering, steering_from_text
from sectors.banking.spec import GENERATOR_ID, PACK
from sectors.journeys import STUDIO_TRAJECTORY_CAP, Note, generate_bundle

__all__ = ["GENERATOR_ID", "STUDIO_TRAJECTORY_CAP", "Note", "generate_banking_bundle"]


def generate_banking_bundle(**kwargs) -> TrajectoryBundle:
    return generate_bundle(PACK, steering_from_text, CorpusSteering(None, None, (), ()), **kwargs)
