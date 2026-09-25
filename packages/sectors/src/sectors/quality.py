"""Quality report v1: the four quality words as measurements.

Complete and comprehensive are measured here. Representative waits for
calibration from data sources (Slice 5) and qualitative for a reliable judge
(Slice 4); both say so rather than showing a number.
"""

from __future__ import annotations

from itertools import groupby

from sectors.lifecycle import LifecycleSpec, lifecycle_checks, structural_checks

QUALITY_VERSION = 1
# Events at or below this prior weight are the rare-but-valid paths worth counting.
RARE_WEIGHT = 0.15


def quality_report(lifecycle: LifecycleSpec, bundle, *, sub_domains: list[str], allowed: tuple[str, ...], cold: bool) -> dict:
    events = {event.event_id: event for event in bundle.events}
    primaries = [item for item in bundle.trajectories if item.parent_trajectory_id is None]
    journeys = [[events[event_id].event_type for event_id in item.event_ids if event_id in events] for item in primaries]
    violations = lifecycle_checks(lifecycle, bundle)
    structural = structural_checks(bundle)

    ended = sum(1 for journey in journeys if journey and lifecycle[journey[-1]].ends_journey)
    filler = 0
    for journey in journeys:
        for name, run in groupby(journey):
            if len(list(run)) > lifecycle[name].repeat:
                filler += 1

    total = len(journeys) or 1
    distinct = len({tuple(journey) for journey in journeys})
    seen = {name for journey in journeys for name in journey}
    coverage = {}
    for domain in sub_domains:
        in_scope = [name for name in allowed if domain in lifecycle[name].sub_domains]
        if in_scope:
            coverage[domain] = round(sum(1 for name in in_scope if name in seen) / len(in_scope), 3)
    rare = {name for name in allowed if lifecycle[name].weight <= RARE_WEIGHT}
    bigrams = {pair for journey in journeys for pair in zip(journey, journey[1:])}

    return {
        "version": QUALITY_VERSION,
        "complete": {
            "passed": not violations,
            "hard_check_violations": len(violations),
            "referential_integrity": not structural,
            "terminal_event_share": round(ended / total, 3),
            "filler_runs": filler,
        },
        "comprehensive": {
            "journeys": len(journeys),
            "distinct_sequences": distinct,
            "distinct_per_100": round(100 * distinct / total, 1),
            "event_type_coverage": coverage,
            "rare_path_share": round(sum(1 for journey in journeys if rare & set(journey)) / total, 3),
            "distinct_transitions": len(bigrams),
        },
        "representative": {
            "status": "unreferenced" if cold else "not_measured",
            "reason": (
                "Cold start: there is no reference to measure against."
                if cold
                else "Measured against calibration data from Slice 5."
            ),
        },
        "qualitative": {"status": "not_measured", "reason": "Measured by the judge with agreement from Slice 4."},
    }
