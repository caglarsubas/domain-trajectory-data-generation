"""Quality report v1: the four quality words as measurements.

Complete and comprehensive are measured here. Representative waits for
calibration from data sources (Slice 5) and qualitative for a reliable judge
(Slice 4); both say so rather than showing a number.

The report is built by an accumulator, so a run generated in batches adds each
batch as it lands and never holds the whole run in memory.
"""

from __future__ import annotations

from itertools import groupby

from sectors.lifecycle import LifecycleSpec, lifecycle_checks, structural_checks

QUALITY_VERSION = 1
# Events at or below this prior weight are the rare-but-valid paths worth counting.
RARE_WEIGHT = 0.15


def primary_journeys(bundle) -> list[list[str]]:
    events = {event.event_id: event.event_type for event in bundle.events}
    return [
        [events[event_id] for event_id in item.event_ids if event_id in events]
        for item in bundle.trajectories
        if item.parent_trajectory_id is None
    ]


class QualityAccumulator:
    def __init__(self, lifecycle: LifecycleSpec, *, sub_domains: list[str], allowed: tuple[str, ...], cold: bool) -> None:
        self.lifecycle = lifecycle
        self.sub_domains = list(sub_domains)
        self.allowed = tuple(allowed)
        self.cold = cold
        self.violations = 0
        self.structural = 0
        self.journeys = 0
        self.ended = 0
        self.filler = 0
        self.rare = 0
        self.sequences: set[str] = set()
        self.seen: set[str] = set()
        self.transitions: set[str] = set()

    def add(self, bundle) -> None:
        lifecycle = self.lifecycle
        self.violations += len(lifecycle_checks(lifecycle, bundle))
        self.structural += len(structural_checks(bundle))
        rare = {name for name in self.allowed if lifecycle[name].weight <= RARE_WEIGHT}
        for journey in primary_journeys(bundle):
            self.journeys += 1
            if journey and lifecycle[journey[-1]].ends_journey:
                self.ended += 1
            for name, run in groupby(journey):
                if len(list(run)) > lifecycle[name].repeat:
                    self.filler += 1
            if rare & set(journey):
                self.rare += 1
            self.sequences.add(">".join(journey))
            self.seen.update(journey)
            self.transitions.update(f"{a}>{b}" for a, b in zip(journey, journey[1:]))

    def report(self) -> dict:
        total = self.journeys or 1
        coverage = {}
        for domain in self.sub_domains:
            in_scope = [name for name in self.allowed if domain in self.lifecycle[name].sub_domains]
            if in_scope:
                coverage[domain] = round(sum(1 for name in in_scope if name in self.seen) / len(in_scope), 3)
        return {
            "version": QUALITY_VERSION,
            "complete": {
                "passed": self.violations == 0,
                "hard_check_violations": self.violations,
                "referential_integrity": self.structural == 0,
                "terminal_event_share": round(self.ended / total, 3),
                "filler_runs": self.filler,
            },
            "comprehensive": {
                "journeys": self.journeys,
                "distinct_sequences": len(self.sequences),
                "distinct_per_100": round(100 * len(self.sequences) / total, 1),
                "event_type_coverage": coverage,
                "rare_path_share": round(self.rare / total, 3),
                "distinct_transitions": len(self.transitions),
            },
            "representative": {
                "status": "unreferenced" if self.cold else "not_measured",
                "reason": (
                    "Cold start: there is no reference to measure against."
                    if self.cold
                    else "Measured against calibration data from Slice 5."
                ),
            },
            "qualitative": {"status": "not_measured", "reason": "Measured by the judge with agreement from Slice 4."},
        }

    def state(self) -> dict:
        return {
            "violations": self.violations,
            "structural": self.structural,
            "journeys": self.journeys,
            "ended": self.ended,
            "filler": self.filler,
            "rare": self.rare,
            "sequences": sorted(self.sequences),
            "seen": sorted(self.seen),
            "transitions": sorted(self.transitions),
        }

    def restore(self, state: dict) -> None:
        for key in ("violations", "structural", "journeys", "ended", "filler", "rare"):
            setattr(self, key, int(state.get(key, 0)))
        self.sequences = set(state.get("sequences", []))
        self.seen = set(state.get("seen", []))
        self.transitions = set(state.get("transitions", []))


def quality_report(lifecycle: LifecycleSpec, bundle, *, sub_domains: list[str], allowed: tuple[str, ...], cold: bool) -> dict:
    accumulator = QualityAccumulator(lifecycle, sub_domains=sub_domains, allowed=allowed, cold=cold)
    accumulator.add(bundle)
    return accumulator.report()
