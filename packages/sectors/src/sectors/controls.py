"""Controls with one known defect, to measure whether a judge can tell a journey from a flawed copy of it.

A judge that gives every journey the top score agrees with itself and with other judges perfectly, and says nothing.
Each control changes one thing in a journey the pack generated, and the pack's own machinery confirms the defect:

- `missing_step`: one event is removed, so a later event's preconditions no longer hold and the replay fails;
- `worse_choice`: at an outcome decision, a legal rival whose simulated chance of reaching the goal is clearly lower
  takes the place of the choice made, and the journey ends there;
- `slow_wait`: one wait is stretched far past its step's range, the behavior rubric's prompt item.

This module works on event types and waits and calls nothing; the studio builds the copies a judge reads.
"""

from __future__ import annotations

from sectors.lifecycle import Walker, allowed_events, apply, satisfied

KINDS = ("missing_step", "worse_choice", "slow_wait")
# A rival counts as worse when its value is at most this share of the choice made: the decision score's near-best bar.
WORSE = 0.9
# A stretched wait is this many times its step's longest wait, and at least a month.
STRETCH = 10.0
MIN_STRETCH_HOURS = 30 * 24.0


def walker_for(pack, domains: list[str]) -> Walker:
    """The pack's policy over a run's scope, uncalibrated: controls are judged against the pack's own process."""
    lifecycle = pack.lifecycle
    scope = [name for name in domains if name in lifecycle.sub_domains] or [pack.default_domain]
    return Walker(lifecycle, allowed=allowed_events(lifecycle, scope, set()), sub_domains=scope)


def first_illegal(lifecycle, types: list[str]) -> int | None:
    """The index of the first event whose preconditions do not hold when the path is replayed, or None."""
    state: dict = {}
    for index, name in enumerate(types):
        spec = lifecycle.get(name)
        if spec is None or not satisfied(spec, state):
            return index
        apply(spec, state)
    return None


def missing_step(lifecycle, types: list[str]) -> dict | None:
    """An inner event whose removal breaks a later event's preconditions, searched from the middle outward."""
    middle = len(types) // 2
    for index in sorted(range(1, len(types) - 1), key=lambda item: abs(item - middle)):
        path = types[:index] + types[index + 1 :]
        broken = first_illegal(lifecycle, path)
        if broken is not None:
            return {"index": index, "removed": types[index], "breaks": path[broken]}
    return None


def slow_wait(lifecycle, types: list[str], hours: list[float]) -> dict | None:
    """A wait from the middle of the journey outward, stretched to ten times its step's longest wait."""
    if len(types) < 2:
        return None
    middle = len(types) // 2
    for index in sorted(range(1, len(types)), key=lambda item: abs(item - middle)):
        spec = lifecycle[types[index]]
        longest = max(spec.dwell_hours[1], (spec.cycle_hours or (0.0, 0.0))[1])
        stretched = max(longest * STRETCH, MIN_STRETCH_HOURS)
        waited = hours[index - 1] if index - 1 < len(hours) else 0.0
        if stretched > 2 * max(waited, 1e-3):
            return {"index": index, "event": types[index], "hours": round(stretched, 1), "was": round(waited, 2), "longest": longest}
    return None


def worse_choice(pack, walker: Walker, types: list[str], *, floor: int, cap: int) -> dict | None:
    """The first outcome decision where a legal rival is clearly worse than the choice made, by simulated value."""
    from sectors.decisions import _Values, decision_steps

    lifecycle = pack.lifecycle
    values = _Values(pack, walker, floor=floor, cap=cap)
    for index, state, counts, rivals in decision_steps(lifecycle, walker, types):
        scored = {}
        for name, _ in rivals:
            after, tally = dict(state), dict(counts)
            apply(lifecycle[name], after)
            tally[name] = tally.get(name, 0) + 1
            scored[name] = values(types[:index] + [name], after, tally)
        taken = types[index]
        rival = min(scored, key=scored.get)
        if rival != taken and scored[taken] > 0 and scored[rival] <= WORSE * scored[taken]:
            return {"index": index, "taken": taken, "rival": rival, "taken_value": scored[taken], "rival_value": scored[rival]}
    return None
