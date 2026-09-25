from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.insurance.spec import LIFECYCLE
from sectors.lifecycle import lifecycle_checks


def insurance_hard_checks(bundle: TrajectoryBundle) -> list[str]:
    """Structural errors, then each trajectory replayed through the insurance state machines. Empty means it may be judged."""
    return lifecycle_checks(LIFECYCLE, bundle)
