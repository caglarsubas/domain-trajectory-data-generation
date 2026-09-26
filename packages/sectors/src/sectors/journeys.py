"""Turn lifecycle walks into a trajectory bundle. Shared by every sector pack."""

from __future__ import annotations

import hashlib
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from trajectory_contract.models import (
    Context,
    Event,
    EventObject,
    GenerationMeta,
    ObjectRecord,
    ObservationStatus,
    Relationship,
    Sample,
    Segment,
    Sequence,
    StateTransition,
    Trajectory,
    TrajectoryBundle,
)

from sectors.calibration import Calibration, representativeness, steps_of
from sectors.jurisdictions import Jurisdiction, get_jurisdiction
from sectors.lifecycle import LifecycleSpec, Path, Walker, allowed_events, dwell
from sectors import rewards
from sectors.quality import quality_report
from sectors.scorers import PASS_AT, Scorer, pass_at_k

STUDIO_TRAJECTORY_CAP = 64
REVISED_MIN_HOURS = 24.0 * 7
PATH_ATTEMPTS = 40

LANG_CURRENCY = {
    "en": "GBP",
    "tr": "TRY",
    "de": "EUR",
    "fr": "EUR",
    "es": "EUR",
    "it": "EUR",
    "nl": "EUR",
    "pt": "EUR",
}


@dataclass(frozen=True)
class Amount:
    low: float
    high: float
    # Seen from the customer's account: credit adds money, debit removes it.
    direction: str
    role: str


class UnsupportedLanguage(ValueError):
    pass


@dataclass(frozen=True)
class PackSpec:
    sector: str
    generator_id: str
    pack_version: str
    lifecycle: LifecycleSpec
    default_domain: str
    languages: tuple[str, ...]
    objects: dict[str, tuple[str, str, str]]
    roles: dict[str, tuple[tuple[str, str], ...]]
    phrases: dict[str, dict[str, str]]
    prompts: dict[str, tuple[str, ...]]
    # Opening user lines keyed by the first event ("@complaint.received"), then by the journey's type, with
    # "*" as the fallback, and the user lines between assistant turns.
    openings: dict[str, dict[str, tuple[str, ...]]]
    follow_ups: dict[str, tuple[str, ...]]
    relationships: tuple[tuple[str, str, str], ...]
    system_events: frozenset[str]
    fixed_channels: dict[str, str]
    default_channels: dict[str, str]
    amounts: dict[str, Amount]
    trajectory_types: tuple[str, ...]
    classify: Callable[[list[str]], str]
    success: Callable[[list[str]], bool]
    subtype: Callable[[str, str, Any], str]
    # What an object did in an event beyond its role, such as the account a purchase debits.
    qualifiers: dict[tuple[str, str], str] = field(default_factory=dict)
    # Hours from event_time to effective_time where they differ, such as a card purchase posting later.
    effective_lag_hours: dict[str, tuple[float, float]] = field(default_factory=dict)
    # A "correctness" revision note removes these events, which the judge found unsupported.
    correctness_drops: tuple[str, ...] = ()
    # What the customer is after, such as a loan; the sequences of one group keep the same intent.
    intent: Callable[[list[str]], str | None] = lambda types: None
    # What `success` means, per language, for decision records that score the chance of reaching it.
    goal: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Note:
    target_type: str
    target_id: str
    stance: str
    comment: str


class _Ids:
    """Record ids. A batch of a larger run prefixes every id, such as `B0003.E00012`, so batches never collide."""

    def __init__(self, namespace: str = "") -> None:
        self.namespace = namespace
        self.counts: dict[str, int] = {}

    def take(self, prefix: str) -> str:
        self.counts[prefix] = self.counts.get(prefix, 0) + 1
        return f"{self.namespace}{prefix}{self.counts[prefix]:05d}"


class _Member:
    """One sequence of a group: the events it narrates and how it scores."""

    def __init__(self, types: list[str], trajectory_id: str, hours: list[float]) -> None:
        self.types = types
        self.trajectory_id = trajectory_id
        # Waits before each step after the first, which the behavior rubric reads.
        self.hours = hours


class _Journey:
    def __init__(self) -> None:
        self.objects: list[ObjectRecord] = []
        self.relationships: list[Relationship] = []
        self.events: list[Event] = []
        self.links: list[EventObject] = []
        self.transitions: list[StateTransition] = []
        self.trajectories: list[Trajectory] = []
        # Each group becomes one sample; a group of one is a sample with a single sequence.
        self.groups: list[list[_Member]] = []


def generate_bundle(
    pack: PackSpec,
    steering_from_text: Callable[[str], Any],
    empty_steering: Any,
    *,
    sub_domains: list[str],
    language: str,
    target_trajectory_count: int,
    event_budget: int | None,
    min_events: int,
    max_events: int,
    max_assistant_turns: int,
    start_mode: str,
    reward_mechanism: str,
    signal_mechanism: str,
    consumer: str,
    target_family: str,
    corpus_text: str = "",
    feedback: list[Note] | list[dict[str, Any]] | None = None,
    revision_notes: list[str] | None = None,
    parent_bundle: TrajectoryBundle | dict[str, Any] | None = None,
    seed: str = "trajectory",
    materialization_cap: int = STUDIO_TRAJECTORY_CAP,
    group_size: int = 1,
    progress: Callable[..., None] | None = None,
    id_prefix: str = "",
    jurisdiction: str = "neutral",
    corpus_steering: Any | None = None,
    calibration: Calibration | dict | None = None,
    episodes: bool = False,
    operations: list[dict] | None = None,
    decisions: bool = False,
) -> TrajectoryBundle:
    lang = language_code(language)
    if lang not in pack.languages:
        raise UnsupportedLanguage(f"{pack.sector} supports {', '.join(pack.languages)}, not {language}")
    lifecycle = pack.lifecycle
    domains = [name for name in sub_domains if name in lifecycle.sub_domains] or [pack.default_domain]
    cold = start_mode == "cold"
    # Reviewed facts steer when the caller has them; otherwise the text does, as before facts existed.
    steering = empty_steering if cold else (corpus_steering if corpus_steering is not None else steering_from_text(corpus_text or ""))
    profile = get_jurisdiction(jurisdiction)
    calibrated = calibration if isinstance(calibration, Calibration) else (Calibration.from_dict(calibration) if calibration else None)
    if calibrated is not None and calibrated.empty:
        calibrated = None
    notes = _coerce_notes(feedback)
    revisions = [item for item in (revision_notes or []) if item]
    event_index, trajectory_index = _parent_index(parent_bundle)
    dropped, kept, revised, dropped_kinds, kept_kinds = _interpret(pack, notes, event_index, trajectory_index)
    if any("correctness" in item.lower() for item in revisions):
        dropped.update(pack.correctness_drops)
    enrich = any("helpfulness" in item.lower() for item in revisions)
    notes_report = _notes_report(pack, notes, event_index, trajectory_index, revisions, dropped, kept)
    allowed = allowed_events(lifecycle, domains, dropped, extra=tuple(kept))
    named = tuple(name for name in steering.events if name in allowed)
    if calibrated is not None:
        calibrated = calibrated.projected(allowed)
    walker = Walker(lifecycle, allowed=allowed, sub_domains=domains, named=named, kept=tuple(kept), calibration=calibrated)
    # Separate streams: timing or wording changes never change which journeys are drawn.
    paths, clock, words = _rng(seed), _rng(seed + "|time"), _rng(seed + "|text")
    size = min(max(int(group_size), 1), MAX_GROUP_SIZE)
    requested = max(int(target_trajectory_count), 1)
    # The cap counts every sequence the studio stores, so larger groups mean fewer prompts.
    limit = min(requested, max(int(materialization_cap) // size, 1))
    floor, cap = _bounds(min_events, max_events)
    # A helpfulness note lengthens the journeys that can go on. One the domain ends, such as a declined
    # application, keeps the requested minimum, or the note would leave out those outcomes.
    ended_floor = floor
    if enrich:
        floor = min(floor + 1, cap)
    remaining = None if event_budget is None else max(int(event_budget), 1)
    ids = _Ids(id_prefix)
    built: list[_Journey] = []
    limited_by: str | None = None
    context = _Context(pack, domains, lang, language, steering, cold, revised, notes, revisions, reward_mechanism,
                       signal_mechanism, consumer, target_family, max(int(max_assistant_turns), 1), clock, words, ids,
                       jurisdiction=profile, calibration=calibrated)

    report = progress or (lambda *args, **kwargs: None)
    report(0, limit, "Drawing journeys.")
    while len(built) < limit:
        report(len(built), limit, f"Drew {len(built)} of {limit} {'groups' if size > 1 else 'journeys'}.")
        room = cap if remaining is None else min(cap, remaining)
        if built and room < floor:
            limited_by = "event_budget"
            break
        path = _choose(walker, pack, paths, floor=min(floor, room), ended_floor=min(ended_floor, room), cap=room,
                       dropped=dropped_kinds, kept=kept_kinds)
        if not path.steps:
            # Nothing is legal from the start, for example when notes dropped every opening event.
            break
        if size > 1:
            split, rollouts = _rollouts(walker, pack, paths, path, size, floor=floor, cap=cap, dropped=dropped_kinds)
            extra = sum(len(item.steps) - split for item in rollouts)
            if remaining is not None and len(path.steps) + extra > remaining:
                rollouts, extra = [], 0
            built.append(_materialize_group(context, path=path, split=split, rollouts=rollouts))
        else:
            branch = walker.branch(path, paths, cap=cap)
            extra = 0
            if branch is not None:
                extra = len(branch[0].steps) - branch[1]
                if remaining is not None and len(path.steps) + extra > remaining:
                    branch, extra = None, 0
            built.append(_materialize(context, path=path, branch=branch))
        if remaining is not None:
            remaining -= len(path.steps) + extra
            if remaining <= 0:
                limited_by = "event_budget"
                break

    if limited_by is None and len(built) < requested:
        limited_by = "studio_cap"
    samples = [_group_sample(context, group) for journey in built for group in journey.groups]
    groups = [group for journey in built for group in journey.groups]
    # Decision values are simulated only when the signal or the decision records need them.
    scorer = Scorer(pack, walker, domains=domains, floor=floor, cap=cap, decisions=decisions or signal_mechanism == "decision_score")
    reward_summary = score_samples(samples, groups, reward_mechanism, signal_mechanism, scorer)
    events = [event for journey in built for event in journey.events]
    bundle = TrajectoryBundle(
        objects=[item for journey in built for item in journey.objects],
        relationships=[item for journey in built for item in journey.relationships],
        events=events,
        event_objects=[item for journey in built for item in journey.links],
        state_transitions=[item for journey in built for item in journey.transitions],
        trajectories=[item for journey in built for item in journey.trajectories],
        samples=samples,
        generation=GenerationMeta(
            generator_id=pack.generator_id,
            pack_version=pack.pack_version,
            group_size=size,
            rewards=reward_summary,
            requested_trajectories=requested,
            primary_trajectories=len(built),
            alternative_trajectories=sum(len(journey.trajectories) - 1 for journey in built),
            event_count=len(events),
            limited_by=limited_by,
            steering=None if cold else {
                **steering.report(),
                "weighted_events": list(named),
                "outside_scope_events": [name for name in steering.events if name not in allowed],
            },
            notes=notes_report,
            jurisdiction=profile.id,
            calibration=calibrated.summary() if calibrated is not None else None,
        ),
    )
    assert bundle.generation is not None
    bundle.generation.quality = quality_report(lifecycle, bundle, sub_domains=domains, allowed=allowed, cold=cold)
    if episodes:
        from sectors.episodes import build_episodes, summarize

        bundle.episodes = build_episodes(pack, bundle, language=language, seed=seed, operations=operations)
        bundle.generation.episodes = summarize(bundle.episodes)
    if decisions:
        from sectors.decisions import build_decisions, summarize as summarize_decisions

        # Values are simulated on their own random streams, so recording decisions changes no journey.
        bundle.decisions = build_decisions(pack, bundle, walker, floor=floor, cap=cap, language=language)
        bundle.generation.decisions = summarize_decisions(bundle.decisions)
    if calibrated is not None:
        kinds = {event.event_id: event.event_type for event in bundle.events}
        primaries = [[kinds[item] for item in trajectory.event_ids] for trajectory in bundle.trajectories if trajectory.parent_trajectory_id is None]
        bundle.generation.quality["representative"] = representativeness(calibrated, steps_of(primaries))
    return bundle


def language_code(language: str) -> str:
    return language.split("-")[0].strip().lower()


@dataclass
class _Context:
    pack: PackSpec
    domains: list[str]
    lang: str
    language: str
    steering: Any
    cold: bool
    revised: dict[str, str]
    notes: list[Note]
    revisions: list[str]
    reward_mechanism: str
    signal_mechanism: str
    consumer: str
    target_family: str
    turns: int
    clock: random.Random
    words: random.Random
    ids: _Ids
    jurisdiction: Jurisdiction | None = None
    calibration: Calibration | None = None


def _choose(
    walker: Walker,
    pack: PackSpec,
    rng: random.Random,
    *,
    floor: int,
    ended_floor: int,
    cap: int,
    dropped: set[str],
    kept: set[str],
) -> Path:
    """Walk until a journey meets the length floor and the trajectory-type notes.

    A journey the domain ended, such as a declined application, only has to reach `ended_floor`, which is
    below `floor` when a revision note asked for longer journeys: those outcomes cannot run longer.
    """
    best: Path | None = None
    best_score: tuple[bool, bool, int] | None = None
    for _ in range(PATH_ATTEMPTS):
        path = walker.walk(rng, floor=floor, cap=cap)
        kind = pack.classify(path.types)
        wanted = kind not in dropped and (not kept or kind in kept)
        ended = bool(path.steps) and pack.lifecycle[path.types[-1]].ends_journey
        long_enough = len(path.steps) >= (ended_floor if ended else floor)
        if wanted and long_enough:
            return path
        score = (wanted, long_enough, len(path.steps))
        if best_score is None or score > best_score:
            best, best_score = path, score
    assert best is not None
    return best


def _materialize(context: _Context, *, path: Path, branch: tuple[Path, int, float] | None) -> _Journey:
    pack = context.pack
    journey = _Journey()
    catalog: dict[str, ObjectRecord] = {}
    state: dict[tuple[str, str], str] = {}
    snapshots: dict[str, dict[tuple[str, str], str]] = {}
    start = _start_time(context.clock)
    _ensure(pack, "party", start, catalog, context.steering, context.ids, journey, context.jurisdiction)
    types = path.types
    primary = _emit(context, types, start=start, advance_first=False, catalog=catalog, state=state,
                    snapshots=snapshots, journey=journey)
    root = catalog["party"].object_id
    trajectory_id = context.ids.take("T")
    journey.trajectories.append(
        Trajectory(
            trajectory_id=trajectory_id,
            root_party_id=root,
            trajectory_type=pack.classify(types),
            start=primary[0].event_time,
            end=primary[-1].event_time,
            observed_or_synthetic="synthetic",
            generator_id=pack.generator_id,
            probability=1.0,
            event_ids=[item.event_id for item in primary],
        )
    )
    journey.groups.append([_member(context, types, trajectory_id, primary)])
    if branch is not None:
        alt_path, split, probability = branch
        anchor = primary[split - 1]
        alt_events = _emit(context, alt_path.types[split:], start=anchor.event_time, advance_first=True,
                           catalog=catalog, state=dict(snapshots[anchor.event_id]), snapshots=snapshots, journey=journey)
        alt_id = f"{trajectory_id}A"
        journey.trajectories.append(
            Trajectory(
                trajectory_id=alt_id,
                root_party_id=root,
                trajectory_type=pack.classify(alt_path.types),
                start=primary[0].event_time,
                end=alt_events[-1].event_time if alt_events else anchor.event_time,
                observed_or_synthetic="alternative",
                parent_trajectory_id=trajectory_id,
                branch_event_id=anchor.event_id,
                generator_id=pack.generator_id,
                probability=round(probability, 2),
                causal_claim=False,
                event_ids=[item.event_id for item in primary[:split]] + [item.event_id for item in alt_events],
            )
        )
        journey.groups.append([_member(context, alt_path.types, alt_id, primary[:split] + alt_events)])
    _relate(pack, catalog, primary[0].event_time, context.ids, journey)
    return journey


def _first_decision(path: Path) -> int:
    """Index of the first step, after the opening, where the machines offered more than one choice.

    A walk with no such step that chose to stop while it could go on decides at its end.
    """
    for index, step in enumerate(path.steps):
        if index and len(step.options) > 1:
            return index
    return len(path.steps)


def _rollouts(
    walker: Walker,
    pack: PackSpec,
    rng: random.Random,
    path: Path,
    size: int,
    *,
    floor: int,
    cap: int,
    dropped: set[str],
) -> tuple[int, list[Path]]:
    """Further sequences for the same prompt: the shared prefix, then a fresh walk from the first decision.

    A rollout keeps the first sequence's intent, such as a loan, so one opening fits the whole group.
    A rollout the domain ended, such as an abandoned application, counts even below the length floor:
    rejecting it would keep only the outcomes that run long, and those are mostly the successes.
    """
    split = _first_decision(path)
    if split < len(path.steps):
        state, counts = path.steps[split].state_before, path.steps[split].counts_before
    elif path.stopped:
        state, counts = path.state, path.counts
    else:
        return split, []
    intent = pack.intent(path.types)
    rollouts = []
    for _ in range(size - 1):
        best: Path | None = None
        for _attempt in range(PATH_ATTEMPTS):
            walked = walker.walk(rng, floor=floor, cap=cap, state=state, counts=counts, prefix=path.steps[:split])
            fits = pack.intent(walked.types) in (None, intent)
            wanted = pack.classify(walked.types) not in dropped
            ended = bool(walked.steps) and pack.lifecycle[walked.types[-1]].ends_journey
            best = walked if best is None else best
            if fits and wanted and (len(walked.steps) >= floor or ended):
                best = walked
                break
        assert best is not None
        rollouts.append(best)
    return split, rollouts


def _materialize_group(context: _Context, *, path: Path, split: int, rollouts: list[Path]) -> _Journey:
    pack = context.pack
    journey = _Journey()
    catalog: dict[str, ObjectRecord] = {}
    state: dict[tuple[str, str], str] = {}
    snapshots: dict[str, dict[tuple[str, str], str]] = {}
    start = _start_time(context.clock)
    _ensure(pack, "party", start, catalog, context.steering, context.ids, journey, context.jurisdiction)
    primary = _emit(context, path.types, start=start, advance_first=False, catalog=catalog, state=state,
                    snapshots=snapshots, journey=journey)
    root = catalog["party"].object_id
    trajectory_id = context.ids.take("T")
    group_id = _swap_prefix(trajectory_id, "T", "G")
    journey.trajectories.append(
        Trajectory(
            trajectory_id=trajectory_id,
            root_party_id=root,
            trajectory_type=pack.classify(path.types),
            start=primary[0].event_time,
            end=primary[-1].event_time,
            observed_or_synthetic="synthetic",
            generator_id=pack.generator_id,
            probability=1.0,
            group_id=group_id,
            event_ids=[item.event_id for item in primary],
        )
    )
    members = [_member(context, path.types, trajectory_id, primary)]
    anchor = primary[split - 1] if rollouts else None
    for number, rollout in enumerate(rollouts, start=2):
        assert anchor is not None
        # A rollout may end at the decision point itself: the customer went no further.
        probability = None
        if len(rollout.steps) > split:
            step = rollout.steps[split]
            probability = round(dict(step.options)[step.event_type] / sum(weight for _, weight in step.options), 2)
        events = _emit(context, rollout.types[split:], start=anchor.event_time, advance_first=True, catalog=catalog,
                       state=dict(snapshots[anchor.event_id]), snapshots=snapshots, journey=journey)
        rollout_id = f"{trajectory_id}R{number:02d}"
        journey.trajectories.append(
            Trajectory(
                trajectory_id=rollout_id,
                root_party_id=root,
                trajectory_type=pack.classify(rollout.types),
                start=primary[0].event_time,
                end=events[-1].event_time if events else anchor.event_time,
                observed_or_synthetic="alternative",
                parent_trajectory_id=trajectory_id,
                branch_event_id=anchor.event_id,
                generator_id=pack.generator_id,
                probability=probability,
                causal_claim=False,
                group_id=group_id,
                event_ids=[item.event_id for item in primary[:split]] + [item.event_id for item in events],
            )
        )
        members.append(_member(context, rollout.types, rollout_id, primary[:split] + events))
    journey.groups.append(members)
    _relate(pack, catalog, primary[0].event_time, context.ids, journey)
    return journey


def _swap_prefix(record_id: str, old: str, new: str) -> str:
    namespace, _, local = record_id.rpartition(".")
    swapped = new + local[len(old):] if local.startswith(old) else local
    return f"{namespace}.{swapped}" if namespace else swapped


def _member(context: _Context, types: list[str], trajectory_id: str, events: list[Event]) -> _Member:
    hours = [(later.event_time - earlier.event_time).total_seconds() / 3600 for earlier, later in zip(events, events[1:])]
    return _Member(types, trajectory_id, hours)


# Relative likelihood of a journey starting on each weekday (Monday first) and in each hour.
WEEKDAY_WEIGHTS = (1.0, 1.0, 1.0, 1.0, 0.95, 0.55, 0.4)
HOUR_WEIGHTS = (
    0.03, 0.02, 0.02, 0.02, 0.03, 0.08, 0.2, 0.45, 0.7, 0.9, 1.0, 1.0,
    0.95, 1.0, 0.95, 0.9, 0.85, 0.8, 0.8, 0.85, 0.8, 0.6, 0.35, 0.12,
)
START_WINDOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _start_time(rng: random.Random) -> datetime:
    while True:
        day = START_WINDOW + timedelta(days=rng.randrange(366))
        if rng.random() < WEEKDAY_WEIGHTS[day.weekday()]:
            break
    hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
    return day + timedelta(hours=hour, minutes=rng.randrange(60), seconds=rng.randrange(60))


def _emit(
    context: _Context,
    types: list[str],
    *,
    start: datetime,
    advance_first: bool,
    catalog: dict[str, ObjectRecord],
    state: dict[tuple[str, str], str],
    snapshots: dict[str, dict[tuple[str, str], str]],
    journey: _Journey,
) -> list[Event]:
    pack, rng, ids, steering = context.pack, context.clock, context.ids, context.steering
    # A jurisdiction's currency is the study's choice and wins; then the documents; then the language.
    profile_currency = context.jurisdiction.currency if context.jurisdiction is not None else None
    currency = profile_currency or steering.currency or LANG_CURRENCY.get(context.lang, "GBP")
    cursor = start
    emitted: list[Event] = []
    party = catalog["party"].object_id
    for position, event_type in enumerate(types):
        spec = pack.lifecycle[event_type]
        if position > 0 or advance_first:
            low, high = spec.dwell_hours
            if event_type in context.revised:
                low = max(low, REVISED_MIN_HOURS)
                high = max(high, low)
            # Observed durations for this step when a data source has enough of them; the pack's range otherwise.
            observed = context.calibration.dwell_hours(rng, types[position - 1] if position else None, event_type) if context.calibration else None
            hours = max(observed, low) if observed is not None and event_type in context.revised else observed
            cursor = cursor + max(timedelta(hours=hours if hours is not None else dwell(rng, low, high)), timedelta(seconds=1))
        when = cursor
        for kind, _role in pack.roles[event_type]:
            _ensure(pack, kind, when, catalog, steering, ids, journey, context.jurisdiction)
        lag = pack.effective_lag_hours.get(event_type)
        amount = pack.amounts.get(event_type)
        event = Event(
            event_id=ids.take("E"),
            event_type=event_type,
            event_time=when,
            effective_time=when + timedelta(hours=dwell(rng, *lag)) if lag else when,
            recorded_at=when + timedelta(seconds=dwell(rng, 0.5, 90.0)),
            timestamp_precision="second",
            status="simulated",
            amount=round(rng.uniform(amount.low, amount.high), 2) if amount else None,
            currency=currency if amount else None,
            direction=amount.direction if amount else None,
            amount_role=amount.role if amount else None,
            channel_id=_channel(pack, event_type, steering.channel),
            case_id=_swap_prefix(party, "P", "C"),
            session_id=_swap_prefix(party, "P", "S"),
            source_system=pack.generator_id,
            observation_status=ObservationStatus.simulated,
        )
        journey.events.append(event)
        for kind, role in pack.roles[event_type]:
            journey.links.append(
                EventObject(
                    event_id=event.event_id,
                    object_id=catalog[kind].object_id,
                    object_role=role,
                    qualifier=pack.qualifiers.get((event_type, kind)),
                )
            )
        for effect in spec.sets:
            obj = catalog.get(effect.kind) or _ensure(pack, effect.kind, when, catalog, steering, ids, journey, context.jurisdiction)
            key = (obj.object_id, effect.dimension)
            before = state.get(key)
            if before == effect.state:
                continue
            state[key] = effect.state
            journey.transitions.append(
                StateTransition(
                    event_id=event.event_id,
                    object_id=obj.object_id,
                    state_dimension=effect.dimension,
                    state_before=before,
                    state_after=effect.state,
                    reason=event_type,
                    confidence=1.0,
                )
            )
        snapshots[event.event_id] = dict(state)
        emitted.append(event)
    return emitted


def _ensure(
    pack: PackSpec,
    kind: str,
    when: datetime,
    catalog: dict[str, ObjectRecord],
    steering: Any,
    ids: _Ids,
    journey: _Journey,
    profile: Jurisdiction | None = None,
) -> ObjectRecord:
    existing = catalog.get(kind)
    if existing is not None:
        return existing
    prefix, object_type, default = pack.objects[kind]
    attributes: dict[str, Any] = {"synthetic": True}
    if kind == "offering" and steering.terms:
        attributes["corpus_terms"] = list(steering.terms)
    subtype = pack.subtype(kind, default, steering)
    if profile is not None and kind != "party" and subtype in profile.products:
        # The jurisdiction's local name for the product, such as "vadesiz hesap" for a Turkish current account.
        attributes["product_name"] = profile.products[subtype]
    record = ObjectRecord(
        object_id=ids.take(prefix),
        object_type=object_type,
        subtype=subtype,
        valid_from=when,
        attributes=attributes,
        source_system=pack.generator_id,
        pii_class="synthetic" if kind == "party" else "none",
    )
    catalog[kind] = record
    journey.objects.append(record)
    return record


def _relate(pack: PackSpec, catalog: dict[str, ObjectRecord], when: datetime, ids: _Ids, journey: _Journey) -> None:
    for subject, predicate, target in pack.relationships:
        if subject in catalog and target in catalog:
            journey.relationships.append(
                Relationship(
                    relationship_id=ids.take("R"),
                    subject_id=catalog[subject].object_id,
                    predicate=predicate,
                    object_id=catalog[target].object_id,
                    valid_from=when,
                    source=pack.generator_id,
                    confidence=1.0,
                )
            )


def _group_sample(context: _Context, members: list[_Member]) -> Sample:
    """One prompt, one opening, and one sequence per member. The first member sets the prompt."""
    pack, words, ids, steering = context.pack, context.words, context.ids, context.steering
    phrases = pack.phrases[context.lang]
    if context.cold:
        reference = "Cold start. No warm-start corpus was used."
    else:
        parts = []
        if steering.terms:
            parts.append("Warm corpus terms: " + ", ".join(steering.terms) + ".")
        if steering.events:
            parts.append("Corpus events: " + ", ".join(steering.events) + ".")
        reference = " ".join(parts) or f"Warm corpus attached. No {pack.sector} terms matched."
    # Consumer and target family choose export parts, not text, so they stay out of the prompt.
    system = " ".join(
        (
            f"Synthetic {pack.sector} study. Language {context.language}.",
            f"Sub-domains: {', '.join(context.domains)}.",
            f"Reward {context.reward_mechanism}. Signal {context.signal_mechanism}.",
            reference,
            f"Jurisdiction {context.jurisdiction.label}. KYC: {' '.join(context.jurisdiction.kyc)}" if context.jurisdiction else "",
            f"Generator {pack.generator_id}, pack {pack.pack_version}.",
            "Alternative branches are simulated, not causal counterfactuals.",
        )
    )
    # Reviewer notes stay in the system segment. They are never trainable text.
    if context.notes:
        system += " Notes: " + " | ".join(f"{note.stance} {note.target_type} {note.comment}" for note in context.notes)
    if context.revisions:
        system += " Revision notes: " + " | ".join(context.revisions)
    system = system[:2000]
    first = members[0].types
    openings = pack.openings[context.lang]
    opening = words.choice(openings.get(f"@{first[0]}") or openings.get(pack.classify(first)) or openings["*"])
    prompt = words.choice(pack.prompts[context.lang])
    sequences = []
    for member in members:
        segments = [
            Segment(segment_id=ids.take("G"), role="system", text=system, trainable=False),
            Segment(segment_id=ids.take("G"), role="user", text=opening, trainable=False),
        ]
        for index, turn in enumerate(_turns([phrases[item] for item in member.types], context.turns)):
            if index:
                segments.append(
                    Segment(segment_id=ids.take("G"), role="user", text=words.choice(pack.follow_ups[context.lang]), trainable=False)
                )
            segments.append(Segment(segment_id=ids.take("G"), role="assistant", text=turn, trainable=True))
        sequences.append(
            Sequence(
                sequence_id=ids.take("Q"),
                trajectory_id=member.trajectory_id,
                contexts=[Context(context_id=ids.take("X"), segments=segments)],
            )
        )
    return Sample(sample_id=ids.take("S"), trajectory_id=members[0].trajectory_id, prompt=prompt, sequences=sequences)


def _turns(sentences: list[str], turns: int) -> list[str]:
    """Group whole sentences into at most `turns` assistant turns of near-equal size."""
    count = max(1, min(turns, len(sentences)))
    size, extra = divmod(len(sentences), count)
    grouped: list[str] = []
    start = 0
    for index in range(count):
        end = start + size + (1 if index < extra else 0)
        grouped.append(" ".join(sentences[start:end]))
        start = end
    return grouped


MAX_GROUP_SIZE = 16
OVERLONG_TOKENS = 400
# Penalty rules detect; strategies act. Every rule starts in record-only mode: it is flagged and
# counted, and changes no reward, mask, or advantage until it is switched to a masking strategy.
ENFORCED_RULES: frozenset[str] = frozenset()


def token_estimate(text: str) -> int:
    return round(len(text.split()) * 4 / 3)


def _flag(segments: list[Segment]) -> None:
    previous = None
    for segment in segments:
        if segment.role != "assistant":
            continue
        if not segment.text.strip():
            segment.flagged_reason = "empty_turn"
        elif segment.text == previous:
            segment.flagged_reason = "repeated_turn"
        elif token_estimate(segment.text) > OVERLONG_TOKENS:
            segment.flagged_reason = "overlong_turn"
        previous = segment.text


def score_samples(samples: list[Sample], groups: list[list[_Member]], mechanism: str, signal: str, scorer: Scorer) -> dict:
    """Rewards and advantages per group with the shared MiMo mechanisms, then segment advantages across the run.

    Every sequence is scored by all five signals; the run's signal decides pass or fail, and the solution and
    behavior rubrics are the solution and behavior terms of the reward.
    """
    flags: dict[str, int] = {}
    accepted = judged = passes = total = 0
    all_sequences: list[Sequence] = []
    tallies: dict[str, list[float]] = {}
    for sample, members in zip(samples, groups):
        scored = [scorer.score(member.types, member.hours) for member in members]
        passed = [found[signal]["passed"] for found in scored]
        solution = [found["solution_rubric"]["score"] for found in scored]
        behavior = [found["behavior_rubric"]["score"] for found in scored]
        for found in scored:
            for name, verdict in found.items():
                tallies.setdefault(name, []).append(verdict["score"])
                tallies.setdefault(f"{name}.passed", []).append(float(verdict["passed"]))
        # Each group is k attempts at one prompt by the generator's policy: pass@k per signal, averaged over groups.
        for name in scored[0]:
            hits = sum(found[name]["passed"] for found in scored)
            for k in PASS_AT:
                if k <= len(scored):
                    tallies.setdefault(f"{name}.pass@{k}", []).append(pass_at_k(len(scored), hits, k))
        lengths = []
        for sequence in sample.sequences:
            segments = [segment for context in sequence.contexts for segment in context.segments]
            _flag(segments)
            for segment in segments:
                if segment.flagged_reason:
                    flags[segment.flagged_reason] = flags.get(segment.flagged_reason, 0) + 1
            sequence.token_estimate = sum(token_estimate(segment.text) for segment in segments if segment.trainable)
            lengths.append(sequence.token_estimate)
        binary = [1.0 if ok else 0.0 for ok in passed]
        quality = [None] * len(members)
        if mechanism == "groupwise_reward_synthesis":
            reward = [rewards.multiplicative_reward(ok, sol, beh) for ok, sol, beh in zip(passed, solution, behavior)]
            advantage = rewards.group_advantages(reward)
        elif mechanism == "groupwise_advantage_redistribution":
            reward = binary
            best = max((sol * beh for ok, sol, beh in zip(passed, solution, behavior) if ok), default=1.0)
            quality = [(sol * beh) / best if ok else None for ok, sol, beh in zip(passed, solution, behavior)]
            advantage = rewards.redistribute(reward, passed, [value or 1.0 for value in quality])
        elif mechanism == "group_relative_length_penalty":
            reward = rewards.length_penalty(binary, passed, lengths)
            advantage = rewards.group_advantages(reward)
        else:
            reward = binary
            advantage = rewards.group_advantages(reward)
        survives = []
        for sequence, value, adv, ok, sol, beh, factor, found in zip(sample.sequences, reward, advantage, passed, solution, behavior, quality, scored):
            sequence.signals = found
            sequence.reward = round(value, 4)
            sequence.advantage = round(adv, 4)
            sequence.outcome = "pass" if ok else "fail"
            sequence.solution_score = round(sol, 4)
            sequence.behavior_score = round(beh, 4)
            sequence.quality_factor = None if factor is None else round(factor, 4)
            survives.append(
                [
                    [segment.trainable and segment.flagged_reason not in ENFORCED_RULES for segment in context.segments if segment.trainable]
                    for context in sequence.contexts
                ]
            )
        dropped = rewards.cascade(survives)
        for sequence, contexts, gone in zip(sample.sequences, dropped.context_dropped, dropped.sequence_dropped):
            sequence.dropped = gone
            if gone:
                sequence.advantage = 0.0
            for context, context_gone in zip(sequence.contexts, contexts):
                context.dropped = context_gone
        sample.group_accepted = False if dropped.rejected else rewards.group_accepted(passed)
        sample.group_pass_rate = round(sum(passed) / len(passed), 4)
        if sample.group_accepted is not None:
            judged += 1
            accepted += int(sample.group_accepted)
        passes += sum(passed)
        total += len(passed)
        all_sequences.extend(sample.sequences)

    trainable = [[segment for context in sequence.contexts for segment in context.segments if segment.trainable] for sequence in all_sequences]
    enforce = mechanism == "segment_penalty"
    per_segment = rewards.segment_advantages(
        [sequence.advantage or 0.0 for sequence in all_sequences],
        [[enforce and segment.flagged_reason in ENFORCED_RULES for segment in row] for row in trainable],
        [[token_estimate(segment.text) for segment in row] for row in trainable],
    )
    for sequence, row, values in zip(all_sequences, trainable, per_segment):
        for segment, value in zip(row, values):
            segment.advantage = round(value, 4)
        sequence.mask = [
            1 if segment.trainable and not (sequence.dropped or segment.flagged_reason in ENFORCED_RULES) else 0
            for context in sequence.contexts
            for segment in context.segments
        ]
    return {
        "mechanism": mechanism,
        "signal": signal,
        "signals": {
            name: {
                "mean": round(sum(values) / len(values), 4),
                "pass_rate": round(sum(tallies[f"{name}.passed"]) / len(values), 4),
                "sequences": len(values),
                "pass_at_k": {str(k): round(sum(tallies[f"{name}.pass@{k}"]) / len(tallies[f"{name}.pass@{k}"]), 4) for k in PASS_AT if f"{name}.pass@{k}" in tallies},
                "groups": len(samples),
            }
            for name, values in tallies.items()
            if "." not in name
        },
        "groups": len(samples),
        "groups_with_signal": judged,
        "accepted_groups": accepted,
        "pass_rate": round(passes / total, 4) if total else None,
        "penalty_mode": "record",
        "flags": flags,
        "note": None if judged else "Groups of one carry no group-relative signal; set a group size above 1.",
    }


def _interpret(
    pack: PackSpec,
    notes: list[Note],
    event_index: dict[str, str],
    trajectory_index: dict[str, str],
) -> tuple[set[str], list[str], dict[str, str], set[str], set[str]]:
    dropped: set[str] = set()
    kept: list[str] = []
    revised: dict[str, str] = {}
    dropped_kinds: set[str] = set()
    kept_kinds: set[str] = set()
    known = set(pack.lifecycle.namespace)
    for note in notes:
        if note.target_type == "event":
            event_type = event_index.get(note.target_id)
            if event_type is None and note.target_id in known:
                event_type = note.target_id
            if event_type is None:
                continue
            if note.stance == "drop":
                dropped.add(event_type)
            elif note.stance == "keep" and event_type not in kept:
                kept.append(event_type)
            elif note.stance == "revise":
                revised[event_type] = note.comment
        elif note.target_type == "trajectory":
            kind = trajectory_index.get(note.target_id, note.target_id)
            if kind not in pack.trajectory_types:
                continue
            if note.stance == "drop":
                dropped_kinds.add(kind)
            elif note.stance == "keep":
                kept_kinds.add(kind)
    dropped -= set(kept)
    return dropped, kept, revised, dropped_kinds, kept_kinds


def _notes_report(
    pack: PackSpec,
    notes: list[Note],
    event_index: dict[str, str],
    trajectory_index: dict[str, str],
    revisions: list[str],
    dropped: set[str],
    kept: list[str],
) -> dict[str, Any] | None:
    """What each note and revision note did to this run, in words the studio can show."""
    if not notes and not revisions:
        return None
    known = set(pack.lifecycle.namespace)
    applied = []
    for note in notes:
        effect = "Added to the sample prompts."
        if note.target_type == "event":
            event_type = event_index.get(note.target_id) or (note.target_id if note.target_id in known else None)
            if event_type is None:
                effect = "Added to the sample prompts; the event is not in this pack."
            elif note.stance == "drop":
                effect = f"Leaves out {event_type}." if event_type in dropped else f"Overruled: a keep note weights {event_type} in."
            elif note.stance == "keep":
                effect = f"Weights {event_type} in."
            else:
                effect = f"Rewrites the sample text at {event_type}."
        elif note.target_type == "trajectory":
            kind = trajectory_index.get(note.target_id, note.target_id)
            if kind not in pack.trajectory_types:
                effect = "Added to the sample prompts; the journey kind is not in this pack."
            elif note.stance == "drop":
                effect = f"Draws fewer {kind} journeys."
            elif note.stance == "keep":
                effect = f"Draws more {kind} journeys."
        applied.append({"target_type": note.target_type, "target_id": note.target_id, "stance": note.stance, "comment": note.comment, "effect": effect})
    revision_effects = []
    for text in revisions:
        lowered = text.lower()
        effects = []
        if "correctness" in lowered and pack.correctness_drops:
            effects.append(f"Leaves out {', '.join(pack.correctness_drops)}.")
        if "helpfulness" in lowered:
            effects.append("Raises the minimum length by one event for journeys that can go on; "
                           "journeys the domain ends, such as a declined application, keep their length.")
        effects.append("Added to the sample prompts.")
        revision_effects.append({"note": text, "effect": " ".join(effects)})
    return {"feedback": applied, "revisions": revision_effects, "kept_events": list(kept)}


def _parent_index(parent: TrajectoryBundle | dict[str, Any] | None) -> tuple[dict[str, str], dict[str, str]]:
    if parent is None:
        return {}, {}
    data = parent.model_dump(mode="json") if isinstance(parent, TrajectoryBundle) else parent
    events = {item["event_id"]: item["event_type"] for item in data.get("events", [])}
    trajectories = {item["trajectory_id"]: item["trajectory_type"] for item in data.get("trajectories", [])}
    return events, trajectories


def _coerce_notes(feedback: list[Note] | list[dict[str, Any]] | None) -> list[Note]:
    notes: list[Note] = []
    for item in feedback or []:
        if isinstance(item, Note):
            notes.append(item)
        elif hasattr(item, "target_type"):
            notes.append(Note(item.target_type, item.target_id, item.stance, item.comment))
        else:
            notes.append(Note(item["target_type"], item["target_id"], item["stance"], item["comment"]))
    return notes


def _bounds(min_events: int, max_events: int) -> tuple[int, int]:
    high = max(int(max_events), 1)
    low = max(int(min_events), 1)
    return min(low, high), high


def _channel(pack: PackSpec, event_type: str, preferred: str | None) -> str:
    if event_type in pack.system_events:
        return "system"
    if event_type in pack.fixed_channels:
        return pack.fixed_channels[event_type]
    if preferred:
        return preferred
    return pack.default_channels.get(event_type, "web")


def _rng(seed: str) -> random.Random:
    digest = hashlib.sha256(seed.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
