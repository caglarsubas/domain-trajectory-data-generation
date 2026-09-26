from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.lifecycle import lifecycle_checks
from sectors.airline.spec import LIFECYCLE


def airline_hard_checks(bundle: TrajectoryBundle) -> list[str]:
    """Structural errors, then each trajectory replayed through the airline state machines. Empty means it may be judged."""
    return lifecycle_checks(LIFECYCLE, bundle)
