from __future__ import annotations

from trajectory_contract.models import TrajectoryBundle

from sectors.lifecycle import lifecycle_checks
from sectors.telecom.spec import LIFECYCLE


def telecom_hard_checks(bundle: TrajectoryBundle) -> list[str]:
    """Structural errors, then each trajectory replayed through the telecom state machines. Empty means it may be judged."""
    return lifecycle_checks(LIFECYCLE, bundle)
