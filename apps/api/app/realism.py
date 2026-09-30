"""Generated against real on equal terms (decision 26).

A cycle sets generated journeys of the run's own realism sample against real cases from the study's data sources. Both
sides show only what both could hold: the events the data records, within the study's own sub-domains, timed at the
resolution the data records times at. Out-of-order copies of generated journeys, their first recorded step moved to the
end with the times kept, are the control: a judge that does not pick the real case over them does not see realism.
"""

from __future__ import annotations

from sectors.calibration import resolution_of
from sectors.lifecycle import allowed_events

from app.evaluation import generated_steps
from trajectory_contract import TrajectoryBundle

# Out-of-order copies asked per cycle, each against a different real case.
CONTROLS = 4
# Journeys drawn per one kept, since some record too little of what the data holds to be compared.
CANDIDATES = 3


def scope_of(sector, sub_domains: list[str]) -> set[str]:
    """The events a run over these sub-domains can hold, as the generator allows them."""
    domains = [name for name in sub_domains if name in sector.lifecycle.sub_domains] or [sector.lifecycle.sub_domains[0]]
    return set(allowed_events(sector.lifecycle, domains, set()))


def out_of_order(steps: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """A journey's steps with the first moved to the end, each time kept where it was."""
    events = [event for event, _ in steps]
    return list(zip(events[1:] + events[:1], [hours for _, hours in steps]))


def _usable(steps) -> bool:
    return len(steps) >= 2 and len({event for event, _ in steps}) >= 2


def prepare(source: dict | None, store, sector, config: dict, size: int, seed: str) -> dict | None:
    """The realism pairs and controls for one cycle, or None where the study holds no real case its scope can show.

    `source` is what the study's data sources kept (`judging.real_cases`); `store` holds the run's journeys, and `size`
    of them are drawn for realism apart from the rubric sample, each set against a different real case where there are
    enough of them.
    """
    from app.judging import sample_entries

    if not source or size <= 0:
        return None
    scope = scope_of(sector, config["sub_domains"])
    visible = set(source["visible"]) & scope
    real = [[tuple(step) for step in case if step[0] in scope] for case in source["cases"]]
    real = [case for case in real if _usable(case)]
    if not real:
        return None
    resolution = float(source.get("resolution") or 0.0)
    generated = []
    # A journey the data records fewer than two kinds of step of, such as a lapsed reservation, cannot be compared, so
    # more are drawn than needed and the first `size` that can are kept.
    for entry in sample_entries(store.entries(), size * CANDIDATES, f"{seed}|realism"):
        bundle = TrajectoryBundle.model_validate(store.journey(entry["trajectory_id"]))
        steps = generated_steps(bundle, entry["trajectory_id"], visible, resolution)
        if _usable(steps):
            generated.append((entry["trajectory_id"], steps))
        if len(generated) == size:
            break
    if not generated:
        return None
    pairs = [{"trajectory_id": tid, "generated": steps, "real": real[index % len(real)]} for index, (tid, steps) in enumerate(generated)]
    controls = [
        {"trajectory_id": tid, "generated": out_of_order(steps), "real": real[(len(generated) + index) % len(real)]}
        for index, (tid, steps) in enumerate(generated[:CONTROLS])
    ]
    return {
        "pairs": pairs,
        "controls": controls,
        "untimed": list(source.get("untimed") or []),
        "resolution": resolution,
        "sources": list(source.get("sources") or []),
        "cases": len(real),
    }


def source_resolution(cases_by_source: list[list]) -> float:
    """The coarsest resolution among the sources' real cases: both sides are drawn at it, so neither is finer than one
    source can record."""
    found = [resolution_of([hours for case in cases for _, hours in case if hours is not None]) for cases in cases_by_source]
    return max(found) if found else 0.0
