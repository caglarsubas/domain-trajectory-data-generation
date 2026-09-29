"""Calibration people can bring (Slice 15, decision 21): a template for a team's own log, and what it would change.

Telecommunications and insurance have no public source their terms and data allow, so those packs calibrate from a team's
own journeys. Each pack offers its events as a CSV, each with the sub-domains it belongs to and what it means, and an
example log in the one shape the event-log reader takes without guessing: `case_id`, `activity`, `timestamp`, with an
activity named exactly as a pack event. After a log is mapped, its preview says what calibration would change before a
run: the steps whose shares it moves most, the pack's share against the calibrated run's and the data's, the events the
data cannot see, and how far the next-step shares sit from the data with calibration and without.
"""

from __future__ import annotations

import csv
import io
from collections import Counter

from sectors.calibration import Calibration, representativeness, steps_of

EXAMPLE_CASES = 20
PREVIEW_JOURNEYS = 600
PREVIEW_MOVES = 6
# A move smaller than this, or after an event left fewer times than this in either run, is sampling noise at this size.
PREVIEW_MIN_MOVE = 0.05
PREVIEW_MIN_STEPS = 50


def _settings(sector, **overrides) -> dict:
    settings = dict(
        sub_domains=list(sector.sub_domains), language="en", target_trajectory_count=PREVIEW_JOURNEYS, event_budget=None, min_events=4,
        max_events=24, max_assistant_turns=3, start_mode="cold", reward_mechanism="binary_outcome", signal_mechanism="outcome",
        consumer="post_training", target_family="llm", seed="preview", group_size=1, materialization_cap=PREVIEW_JOURNEYS,
    )
    settings.update(overrides)
    return settings


def _paths(bundle) -> list[list[str]]:
    events = {event.event_id: event for event in bundle.events}
    return [[events[item].event_type for item in trajectory.event_ids] for trajectory in bundle.trajectories if trajectory.parent_trajectory_id is None]


def events_csv(sector) -> str:
    """Every event of the pack, with the sub-domains it belongs to and what it means."""
    phrases = sector.pack.phrases.get("en") or {}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["activity", "sub_domains", "meaning"])
    for spec in sector.lifecycle.events:
        writer.writerow([spec.event_type, " ".join(spec.sub_domains), phrases.get(spec.event_type, "")])
    return out.getvalue()


def example_csv(sector, cases: int = EXAMPLE_CASES) -> str:
    """A short log of the pack's own journeys, written as a team's export should be: one row per event, in time order."""
    bundle = sector.generate(**_settings(sector, target_trajectory_count=cases, materialization_cap=cases, seed="log-template"))
    events = {event.event_id: event for event in bundle.events}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["case_id", "activity", "timestamp"])
    primaries = [trajectory for trajectory in bundle.trajectories if trajectory.parent_trajectory_id is None]
    for number, trajectory in enumerate(primaries, start=1):
        for event_id in trajectory.event_ids:
            event = events[event_id]
            writer.writerow([f"case-{number:03d}", event.event_type, event.event_time.strftime("%Y-%m-%dT%H:%M:%SZ")])
    return out.getvalue()


def _counts(paths: list[list[str]]) -> dict[str, Counter]:
    counts: dict[str, Counter] = {}
    for (after, name), count in steps_of(paths).items():
        counts.setdefault(after, Counter())[name] += count
    return counts


def _shares(paths: list[list[str]]) -> dict[str, dict[str, float]]:
    return {after: {name: count / sum(following.values()) for name, count in following.items()} for after, following in _counts(paths).items()}


def preview(sector, calibration: Calibration) -> dict | None:
    """What a data source's calibration would change in a run over every sub-domain, before one is made."""
    if calibration.empty:
        return None
    plain = sector.generate(**_settings(sector))
    calibrated = sector.generate(**_settings(sector, calibration=calibration.as_dict()))
    plain_paths, calibrated_paths = _paths(plain), _paths(calibrated)
    before, after_calibration = _shares(plain_paths), _shares(calibrated_paths)
    left = {name: sum(following.values()) for name, following in _counts(plain_paths).items()}
    left_calibrated = {name: sum(following.values()) for name, following in _counts(calibrated_paths).items()}
    moves = []
    for after, following in after_calibration.items():
        observed = calibration.transitions.get(after)
        if not observed or min(left.get(after, 0), left_calibrated.get(after, 0)) < PREVIEW_MIN_STEPS:
            continue
        # The data's shares among the next steps this run takes there, as the walker reads them.
        seen = {name: observed.get(name, 0) for name in following if observed.get(name)}
        total = sum(seen.values())
        for name in set(following) | set(before.get(after, {})):
            pack, now = before.get(after, {}).get(name, 0.0), following.get(name, 0.0)
            if abs(now - pack) >= PREVIEW_MIN_MOVE:
                moves.append({"after": after, "next": name, "pack": round(pack, 3), "calibrated": round(now, 3),
                              "data": round(seen[name] / total, 3) if total and name in seen else None})
    moves.sort(key=lambda move: -abs(move["calibrated"] - move["pack"]))
    visible = calibration.observed_events
    unseen = sorted({name for path in calibrated_paths for name in path} - visible)
    with_data = representativeness(calibration, steps_of(calibrated_paths))
    without = representativeness(calibration, steps_of(plain_paths))
    return {
        "journeys": PREVIEW_JOURNEYS,
        "sub_domains": list(sector.sub_domains),
        "moves": moves[:PREVIEW_MOVES],
        "unseen": unseen,
        "divergence": {"calibrated": with_data.get("weighted_divergence"), "uncalibrated": without.get("weighted_divergence")},
        "fitness": with_data.get("fitness"),
        "precision": with_data.get("precision"),
    }
