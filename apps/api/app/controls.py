"""Control journeys for a judge cycle: flawed copies of sampled journeys, each defect confirmed by the pack.

`sectors.controls` finds a defect in a journey's event types and waits; this module builds the copy a judge reads.
A copy keeps the original's customer messages and narrates its own events with the pack's templates, so a judge sees
the same kind of journey as the original with one thing wrong. The pack's hard checks confirm each copy: a missing
step must fail them, and a worse choice or a slow wait must pass them, so the only defect is the one named.
"""

from __future__ import annotations

import random
from copy import deepcopy
from datetime import timedelta

from sectors import controls as found
from trajectory_contract.models import Context, Segment, TrajectoryBundle

# The rubrics each defect should lower. A worse choice often ends in a failure, which is as representative as a success,
# so only the decision score should see it. Safety has nothing to do with any of them; pairwise gets its own control.
TARGETS = {
    "missing_step": ("correctness", "helpfulness"),
    "worse_choice": ("decision_score",),
    "slow_wait": ("helpfulness",),
}
# The control a pairwise judge compares with its original, in order of preference: the plainest defect first.
PAIRWISE_ORDER = ("missing_step", "slow_wait", "worse_choice")


def _bounds(config: dict) -> tuple[int, int]:
    high = max(int(config.get("max_events") or 1), 1)
    return min(max(int(config.get("min_events") or 1), 1), high), high


def _path(bundle: TrajectoryBundle, trajectory_id: str) -> tuple[list[str], list[float]]:
    trajectory = next(item for item in bundle.trajectories if item.trajectory_id == trajectory_id)
    events = {event.event_id: event for event in bundle.events}
    journey = [events[item] for item in trajectory.event_ids if item in events]
    hours = [(later.event_time - earlier.event_time).total_seconds() / 3600 for earlier, later in zip(journey, journey[1:])]
    return [event.event_type for event in journey], hours


def _narrated(copy: TrajectoryBundle, trajectory_id: str, pack, lang: str) -> None:
    """Replace the copy's sample text with the templates' narration of its own events, keeping the customer's messages.

    Phrasings are drawn as generation draws them, from a stream seeded by the journey, so a control reads like the
    journeys it is judged beside.
    """
    from sectors.journeys import _details, _sentences, _turns

    trajectory = next(item for item in copy.trajectories if item.trajectory_id == trajectory_id)
    events = {event.event_id: event for event in copy.events}
    journey = [events[item] for item in trajectory.event_ids if item in events]
    lang = lang if lang in pack.phrases else "en"
    sentences = _sentences(pack, lang, [event.event_type for event in journey], _details(lang, journey), random.Random(trajectory_id))
    for sample in copy.samples:
        for sequence in sample.sequences:
            if sequence.trajectory_id != trajectory_id:
                continue
            original = [segment for context in sequence.contexts for segment in context.segments]
            system = [segment for segment in original if segment.role == "system"]
            users = [segment for segment in original if segment.role == "user"]
            assistant = sum(1 for segment in original if segment.role == "assistant") or 1
            segments = list(system) + users[:1]
            for index, text in enumerate(_turns(sentences, assistant)):
                if index and index < len(users):
                    segments.append(users[index])
                segments.append(Segment(segment_id=f"{sequence.sequence_id}.C{index}", role="assistant", text=text, trainable=False))
            sequence.contexts = [Context(context_id=f"{sequence.sequence_id}.C", segments=segments)]
            sequence.outcome = "pass" if pack.success([event.event_type for event in journey]) else "fail"
    copy.samples = [
        sample.model_copy(update={"sequences": [item for item in sample.sequences if item.trajectory_id == trajectory_id]})
        for sample in copy.samples
        if any(item.trajectory_id == trajectory_id for item in sample.sequences)
    ]


def _copy(bundle: TrajectoryBundle, trajectory_id: str) -> tuple[TrajectoryBundle, object, dict]:
    copy = deepcopy(bundle)
    target = next(item for item in copy.trajectories if item.trajectory_id == trajectory_id)
    copy.trajectories = [target]
    return copy, target, {event.event_id: event for event in copy.events}


def _missing_step(bundle, trajectory_id, types, pack) -> tuple[TrajectoryBundle, str] | None:
    hit = found.missing_step(pack.lifecycle, types)
    if hit is None:
        return None
    copy, target, _ = _copy(bundle, trajectory_id)
    target.event_ids = target.event_ids[: hit["index"]] + target.event_ids[hit["index"] + 1 :]
    return copy, f"{hit['removed']} removed, so {hit['breaks']} no longer follows from the steps before it"


def _slow_wait(bundle, trajectory_id, types, hours, pack) -> tuple[TrajectoryBundle, str] | None:
    hit = found.slow_wait(pack.lifecycle, types, hours)
    if hit is None:
        return None
    copy, target, by_id = _copy(bundle, trajectory_id)
    shift = timedelta(hours=hit["hours"] - hit["was"])
    for event_id in target.event_ids[hit["index"] :]:
        by_id[event_id].event_time = by_id[event_id].event_time + shift
    days = hit["hours"] / 24
    return copy, f"the wait before {hit['event']} stretched to {days:.0f} days, against at most {hit['longest']:g} hours"


def _worse_choice(bundle, trajectory_id, types, pack, walker, floor, cap) -> tuple[TrajectoryBundle, str] | None:
    hit = found.worse_choice(pack, walker, types, floor=floor, cap=cap)
    if hit is None:
        return None
    copy, target, by_id = _copy(bundle, trajectory_id)
    event = by_id[target.event_ids[hit["index"]]]
    event.event_type = hit["rival"]
    event.amount = event.currency = event.direction = event.amount_role = None
    # The original outcome's state changes would contradict the rival; the rival's own effects are the pack's.
    copy.state_transitions = [change for change in copy.state_transitions if change.event_id != event.event_id]
    target.event_ids = target.event_ids[: hit["index"] + 1]
    return copy, (
        f"{hit['rival']} in place of {hit['taken']}, then the journey ends: a {hit['rival_value']:.0%} chance of reaching "
        f"the goal against {hit['taken_value']:.0%}"
    )


def build(journeys: list[TrajectoryBundle], sector, config: dict) -> list[dict]:
    """At most one control of each kind, from different sampled journeys where they allow it."""
    from sectors.journeys import language_code

    pack = sector.pack
    domains = list(config.get("sub_domains") or [])
    floor, cap = _bounds(config)
    lang = language_code(config.get("language") or "en")
    walker = found.walker_for(pack, domains)
    primaries = [next(item for item in journey.trajectories if item.parent_trajectory_id is None) for journey in journeys]
    controls: list[dict] = []
    used: set[str] = set()
    for kind in found.KINDS:
        order = sorted(range(len(journeys)), key=lambda index: primaries[index].trajectory_id in used)
        for index in order:
            journey, trajectory_id = journeys[index], primaries[index].trajectory_id
            types, hours = _path(journey, trajectory_id)
            if kind == "missing_step":
                made = _missing_step(journey, trajectory_id, types, pack)
            elif kind == "slow_wait":
                made = _slow_wait(journey, trajectory_id, types, hours, pack)
            else:
                made = _worse_choice(journey, trajectory_id, types, pack, walker, floor, cap)
            if made is None:
                continue
            copy, detail = made
            broken = bool(sector.hard_checks(copy))
            if broken != (kind == "missing_step"):
                continue
            _narrated(copy, trajectory_id, pack, lang)
            controls.append({
                "kind": kind,
                "trajectory_id": trajectory_id,
                "bundle": copy,
                "detail": detail,
                "rubrics": TARGETS[kind],
            })
            used.add(trajectory_id)
            break
    return controls


def pairwise_pick(controls: list[dict]) -> dict | None:
    for kind in PAIRWISE_ORDER:
        for control in controls:
            if control["kind"] == kind:
                return control
    return None

