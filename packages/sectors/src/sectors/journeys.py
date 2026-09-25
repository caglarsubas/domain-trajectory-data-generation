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

from sectors.lifecycle import LifecycleSpec, Path, Walker, allowed_events, dwell
from sectors.quality import quality_report

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


@dataclass(frozen=True)
class Note:
    target_type: str
    target_id: str
    stance: str
    comment: str


class _Ids:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def take(self, prefix: str) -> str:
        self.counts[prefix] = self.counts.get(prefix, 0) + 1
        return f"{prefix}{self.counts[prefix]:05d}"


class _Scored:
    def __init__(self, sample: Sample, types: list[str], success: bool, coverage: float) -> None:
        self.sample = sample
        self.types = types
        self.success = success
        self.length = len(types)
        self.coverage = coverage


class _Journey:
    def __init__(self) -> None:
        self.objects: list[ObjectRecord] = []
        self.relationships: list[Relationship] = []
        self.events: list[Event] = []
        self.links: list[EventObject] = []
        self.transitions: list[StateTransition] = []
        self.trajectories: list[Trajectory] = []
        self.scored: list[_Scored] = []


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
) -> TrajectoryBundle:
    lang = language_code(language)
    if lang not in pack.languages:
        raise UnsupportedLanguage(f"{pack.sector} supports {', '.join(pack.languages)}, not {language}")
    lifecycle = pack.lifecycle
    domains = [name for name in sub_domains if name in lifecycle.sub_domains] or [pack.default_domain]
    cold = start_mode == "cold"
    steering = empty_steering if cold else steering_from_text(corpus_text or "")
    notes = _coerce_notes(feedback)
    revisions = [item for item in (revision_notes or []) if item]
    event_index, trajectory_index = _parent_index(parent_bundle)
    dropped, kept, revised, dropped_kinds, kept_kinds = _interpret(pack, notes, event_index, trajectory_index)
    if any("correctness" in item.lower() for item in revisions):
        dropped.update(pack.correctness_drops)
    enrich = any("helpfulness" in item.lower() for item in revisions)
    allowed = allowed_events(lifecycle, domains, dropped, extra=tuple(kept))
    named = tuple(name for name in steering.events if name in allowed)
    walker = Walker(lifecycle, allowed=allowed, sub_domains=domains, named=named, kept=tuple(kept))
    # Separate streams: timing or wording changes never change which journeys are drawn.
    paths, clock, words = _rng(seed), _rng(seed + "|time"), _rng(seed + "|text")
    requested = max(int(target_trajectory_count), 1)
    limit = min(requested, max(int(materialization_cap), 1))
    floor, cap = _bounds(min_events, max_events)
    if enrich:
        floor = min(floor + 1, cap)
    remaining = None if event_budget is None else max(int(event_budget), 1)
    ids = _Ids()
    built: list[_Journey] = []
    limited_by: str | None = None
    context = _Context(pack, domains, lang, language, steering, cold, revised, notes, revisions, reward_mechanism,
                       signal_mechanism, consumer, target_family, max(int(max_assistant_turns), 1), clock, words, ids)

    while len(built) < limit:
        room = cap if remaining is None else min(cap, remaining)
        if built and room < floor:
            limited_by = "event_budget"
            break
        path = _choose(walker, pack, paths, floor=min(floor, room), cap=room, dropped=dropped_kinds, kept=kept_kinds)
        if not path.steps:
            # Nothing is legal from the start, for example when notes dropped every opening event.
            break
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
    scored = [item for journey in built for item in journey.scored]
    _apply_rewards(scored, reward_mechanism)
    events = [event for journey in built for event in journey.events]
    bundle = TrajectoryBundle(
        objects=[item for journey in built for item in journey.objects],
        relationships=[item for journey in built for item in journey.relationships],
        events=events,
        event_objects=[item for journey in built for item in journey.links],
        state_transitions=[item for journey in built for item in journey.transitions],
        trajectories=[item for journey in built for item in journey.trajectories],
        samples=[item.sample for item in scored],
        generation=GenerationMeta(
            generator_id=pack.generator_id,
            pack_version=pack.pack_version,
            requested_trajectories=requested,
            primary_trajectories=len(built),
            alternative_trajectories=sum(1 for journey in built if len(journey.trajectories) > 1),
            event_count=len(events),
            limited_by=limited_by,
            steering=None if cold else {
                **steering.report(),
                "weighted_events": list(named),
                "outside_scope_events": [name for name in steering.events if name not in allowed],
            },
        ),
    )
    assert bundle.generation is not None
    bundle.generation.quality = quality_report(lifecycle, bundle, sub_domains=domains, allowed=allowed, cold=cold)
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


def _choose(
    walker: Walker,
    pack: PackSpec,
    rng: random.Random,
    *,
    floor: int,
    cap: int,
    dropped: set[str],
    kept: set[str],
) -> Path:
    """Walk until a journey meets the length floor and the trajectory-type notes."""
    best: Path | None = None
    best_score: tuple[bool, bool, int] | None = None
    for _ in range(PATH_ATTEMPTS):
        path = walker.walk(rng, floor=floor, cap=cap)
        kind = pack.classify(path.types)
        wanted = kind not in dropped and (not kept or kind in kept)
        long_enough = len(path.steps) >= floor
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
    _ensure(pack, "party", start, catalog, context.steering, context.ids, journey)
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
    journey.scored.append(_score(context, types, trajectory_id))
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
        journey.scored.append(_score(context, alt_path.types, alt_id))
    _relate(pack, catalog, primary[0].event_time, context.ids, journey)
    return journey


def _score(context: _Context, types: list[str], trajectory_id: str) -> _Scored:
    lifecycle = context.pack.lifecycle
    touched = set(types)
    covered = sum(1 for domain in context.domains if any(domain in lifecycle[name].sub_domains for name in touched))
    return _Scored(
        _sample(context, types=types, trajectory_id=trajectory_id),
        types,
        context.pack.success(types),
        covered / len(context.domains),
    )


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
    currency = steering.currency or LANG_CURRENCY.get(context.lang, "GBP")
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
            cursor = cursor + max(timedelta(hours=dwell(rng, low, high)), timedelta(seconds=1))
        when = cursor
        for kind, _role in pack.roles[event_type]:
            _ensure(pack, kind, when, catalog, steering, ids, journey)
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
            case_id=party.replace("P", "C", 1),
            session_id=party.replace("P", "S", 1),
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
            obj = catalog.get(effect.kind) or _ensure(pack, effect.kind, when, catalog, steering, ids, journey)
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
) -> ObjectRecord:
    existing = catalog.get(kind)
    if existing is not None:
        return existing
    prefix, object_type, default = pack.objects[kind]
    attributes: dict[str, Any] = {"synthetic": True}
    if kind == "offering" and steering.terms:
        attributes["corpus_terms"] = list(steering.terms)
    record = ObjectRecord(
        object_id=ids.take(prefix),
        object_type=object_type,
        subtype=pack.subtype(kind, default, steering),
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


def _sample(context: _Context, *, types: list[str], trajectory_id: str) -> Sample:
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
    system = " ".join(
        (
            f"Synthetic {pack.sector} study. Language {context.language}.",
            f"Sub-domains: {', '.join(context.domains)}.",
            f"Consumer {context.consumer}. Target family {context.target_family}.",
            f"Reward {context.reward_mechanism}. Signal {context.signal_mechanism}.",
            reference,
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
    kind = pack.classify(types)
    openings = pack.openings[context.lang]
    segments = [
        Segment(segment_id=ids.take("G"), role="system", text=system, trainable=False),
        Segment(
            segment_id=ids.take("G"),
            role="user",
            text=words.choice(openings.get(f"@{types[0]}") or openings.get(kind) or openings["*"]),
            trainable=False,
        ),
    ]
    for index, turn in enumerate(_turns([phrases[item] for item in types], context.turns)):
        if index:
            segments.append(
                Segment(segment_id=ids.take("G"), role="user", text=words.choice(pack.follow_ups[context.lang]), trainable=False)
            )
        segments.append(Segment(segment_id=ids.take("G"), role="assistant", text=turn, trainable=True))
    return Sample(
        sample_id=ids.take("S"),
        trajectory_id=trajectory_id,
        prompt=words.choice(pack.prompts[context.lang]),
        sequences=[Sequence(sequence_id=ids.take("Q"), contexts=[Context(context_id=ids.take("X"), segments=segments)])],
    )


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


def _apply_rewards(scored: list[_Scored], mechanism: str) -> None:
    if not scored:
        return
    successes = [1.0 if item.success else 0.0 for item in scored]
    lengths = [max(item.length, 1) for item in scored]
    mean_success = sum(successes) / len(successes)
    median = statistics.median(lengths)
    for item, success, length in zip(scored, successes, lengths):
        sequence = item.sample.sequences[0]
        if mechanism == "groupwise_reward_synthesis":
            sequence.reward = round(success * (0.5 + 0.5 * item.coverage), 4)
        elif mechanism == "group_relative_length_penalty":
            sequence.reward = round(success * min(1.0, median / length), 4)
        elif mechanism == "groupwise_advantage_redistribution":
            sequence.reward = success
            sequence.advantage = round(success - mean_success, 4)
        elif mechanism == "segment_penalty":
            sequence.reward = success
            sequence.mask = [1 if segment.trainable else 0 for context in sequence.contexts for segment in context.segments]
        else:
            sequence.reward = success


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
