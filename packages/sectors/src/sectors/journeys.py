"""Turn lifecycle walks into a trajectory bundle. Shared by every sector pack."""

from __future__ import annotations

import hashlib
import random
import statistics
from dataclasses import dataclass
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
class PackSpec:
    sector: str
    generator_id: str
    lifecycle: LifecycleSpec
    default_domain: str
    objects: dict[str, tuple[str, str, str]]
    roles: dict[str, tuple[tuple[str, str], ...]]
    phrases: dict[str, dict[str, str]]
    prompts: dict[str, str]
    relationships: tuple[tuple[str, str, str], ...]
    system_events: frozenset[str]
    fixed_channels: dict[str, str]
    default_channels: dict[str, str]
    amounts: dict[str, tuple[float, float]]
    trajectory_types: tuple[str, ...]
    classify: Callable[[list[str]], str]
    success: Callable[[list[str]], bool]
    user_line: Callable[[str, str], str]
    subtype: Callable[[str, str, Any], str]
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


class _Journey:
    def __init__(self) -> None:
        self.objects: list[ObjectRecord] = []
        self.relationships: list[Relationship] = []
        self.events: list[Event] = []
        self.links: list[EventObject] = []
        self.transitions: list[StateTransition] = []
        self.trajectories: list[Trajectory] = []
        self.sample: Sample
        self.success = False
        self.length = 0
        self.coverage = 0.0


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
    walker = Walker(
        lifecycle,
        allowed=allowed,
        sub_domains=domains,
        named=tuple(name for name in steering.events if name in allowed),
        kept=tuple(kept),
    )
    rng = _rng(seed)
    requested = max(int(target_trajectory_count), 1)
    limit = min(requested, max(int(materialization_cap), 1))
    floor, cap = _bounds(min_events, max_events)
    if enrich:
        floor = min(floor + 1, cap)
    remaining = None if event_budget is None else max(int(event_budget), 1)
    ids = _Ids()
    built: list[_Journey] = []
    limited_by: str | None = None

    while len(built) < limit:
        room = cap if remaining is None else min(cap, remaining)
        if built and room < floor:
            limited_by = "event_budget"
            break
        path = _choose(walker, pack, rng, floor=min(floor, room), cap=room, dropped=dropped_kinds, kept=kept_kinds)
        if not path.steps:
            # Nothing is legal from the start, for example when notes dropped every opening event.
            break
        branch = walker.branch(path, rng, cap=cap)
        extra = 0
        if branch is not None:
            extra = len(branch[0].steps) - branch[1]
            if remaining is not None and len(path.steps) + extra > remaining:
                branch, extra = None, 0
        journey = _materialize(
            pack,
            path=path,
            branch=branch,
            index=len(built),
            domains=domains,
            language=language,
            steering=steering,
            cold=cold,
            revised=revised,
            notes=notes,
            revisions=revisions,
            reward_mechanism=reward_mechanism,
            signal_mechanism=signal_mechanism,
            consumer=consumer,
            target_family=target_family,
            max_assistant_turns=max_assistant_turns,
            rng=rng,
            ids=ids,
        )
        built.append(journey)
        if remaining is not None:
            remaining -= len(path.steps) + extra
            if remaining <= 0:
                limited_by = "event_budget"
                break

    if limited_by is None and len(built) < requested:
        limited_by = "studio_cap"
    _apply_rewards(built, reward_mechanism)
    events = [event for journey in built for event in journey.events]
    return TrajectoryBundle(
        objects=[item for journey in built for item in journey.objects],
        relationships=[item for journey in built for item in journey.relationships],
        events=events,
        event_objects=[item for journey in built for item in journey.links],
        state_transitions=[item for journey in built for item in journey.transitions],
        trajectories=[item for journey in built for item in journey.trajectories],
        samples=[journey.sample for journey in built],
        generation=GenerationMeta(
            generator_id=pack.generator_id,
            requested_trajectories=requested,
            primary_trajectories=len(built),
            alternative_trajectories=sum(1 for journey in built if len(journey.trajectories) > 1),
            event_count=len(events),
            limited_by=limited_by,
        ),
    )


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


def _materialize(
    pack: PackSpec,
    *,
    path: Path,
    branch: tuple[Path, int, float] | None,
    index: int,
    domains: list[str],
    language: str,
    steering: Any,
    cold: bool,
    revised: dict[str, str],
    notes: list[Note],
    revisions: list[str],
    reward_mechanism: str,
    signal_mechanism: str,
    consumer: str,
    target_family: str,
    max_assistant_turns: int,
    rng: random.Random,
    ids: _Ids,
) -> _Journey:
    journey = _Journey()
    catalog: dict[str, ObjectRecord] = {}
    state: dict[tuple[str, str], str] = {}
    snapshots: dict[str, dict[tuple[str, str], str]] = {}
    lang = language.split("-")[0].lower()
    currency = steering.currency or LANG_CURRENCY.get(lang, "GBP")
    start = datetime(2024, 3, 4, 9, 0, tzinfo=timezone.utc) + timedelta(days=index * 3, minutes=rng.randint(0, 90))
    _ensure(pack, "party", start, catalog, steering, ids, journey)
    types = path.types
    primary = _emit(pack, types, start=start, advance_first=False, catalog=catalog, state=state, snapshots=snapshots,
                    steering=steering, currency=currency, revised=revised, rng=rng, ids=ids, journey=journey)
    root = catalog["party"].object_id
    opened = primary[0].event_time
    trajectory_id = ids.take("T")
    kind = pack.classify(types)
    journey.trajectories.append(
        Trajectory(
            trajectory_id=trajectory_id,
            root_party_id=root,
            trajectory_type=kind,
            start=opened,
            end=primary[-1].event_time,
            observed_or_synthetic="synthetic",
            generator_id=pack.generator_id,
            probability=1.0,
            event_ids=[item.event_id for item in primary],
        )
    )
    if branch is not None:
        alt_path, split, probability = branch
        anchor = primary[split - 1]
        suffix = alt_path.types[split:]
        alt_events = _emit(pack, suffix, start=anchor.event_time, advance_first=True, catalog=catalog,
                           state=dict(snapshots[anchor.event_id]), snapshots=snapshots, steering=steering,
                           currency=currency, revised=revised, rng=rng, ids=ids, journey=journey)
        journey.trajectories.append(
            Trajectory(
                trajectory_id=f"{trajectory_id}A",
                root_party_id=root,
                trajectory_type=pack.classify(alt_path.types),
                start=opened,
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
    _relate(pack, catalog, opened, ids, journey)
    journey.sample = _sample(
        pack,
        types=types,
        kind=kind,
        domains=domains,
        language=language,
        steering=steering,
        cold=cold,
        notes=notes,
        revisions=revisions,
        reward_mechanism=reward_mechanism,
        signal_mechanism=signal_mechanism,
        consumer=consumer,
        target_family=target_family,
        max_assistant_turns=max_assistant_turns,
        ids=ids,
    )
    touched = set(types)
    lifecycle = pack.lifecycle
    covered = sum(1 for domain in domains if any(domain in lifecycle[name].sub_domains for name in touched))
    journey.success = pack.success(types)
    journey.length = len(types)
    journey.coverage = covered / len(domains)
    return journey


def _emit(
    pack: PackSpec,
    types: list[str],
    *,
    start: datetime,
    advance_first: bool,
    catalog: dict[str, ObjectRecord],
    state: dict[tuple[str, str], str],
    snapshots: dict[str, dict[tuple[str, str], str]],
    steering: Any,
    currency: str,
    revised: dict[str, str],
    rng: random.Random,
    ids: _Ids,
    journey: _Journey,
) -> list[Event]:
    cursor = start
    emitted: list[Event] = []
    party = catalog["party"].object_id
    for position, event_type in enumerate(types):
        spec = pack.lifecycle[event_type]
        if position > 0 or advance_first:
            low, high = spec.dwell_hours
            if event_type in revised:
                low = max(low, REVISED_MIN_HOURS)
                high = max(high, low)
            cursor = cursor + max(timedelta(hours=dwell(rng, low, high)), timedelta(seconds=1))
        when = cursor
        for kind, _role in pack.roles[event_type]:
            _ensure(pack, kind, when, catalog, steering, ids, journey)
        amount, unit = _money(pack, rng, event_type, currency)
        event = Event(
            event_id=ids.take("E"),
            event_type=event_type,
            event_time=when,
            effective_time=when,
            recorded_at=when + timedelta(seconds=2),
            timestamp_precision="second",
            status="simulated",
            amount=amount,
            currency=unit,
            channel_id=_channel(pack, event_type, steering.channel),
            case_id=party.replace("P", "C", 1),
            session_id=party.replace("P", "S", 1),
            source_system=pack.generator_id,
            observation_status=ObservationStatus.simulated,
        )
        journey.events.append(event)
        for kind, role in pack.roles[event_type]:
            journey.links.append(EventObject(event_id=event.event_id, object_id=catalog[kind].object_id, object_role=role))
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


def _sample(
    pack: PackSpec,
    *,
    types: list[str],
    kind: str,
    domains: list[str],
    language: str,
    steering: Any,
    cold: bool,
    notes: list[Note],
    revisions: list[str],
    reward_mechanism: str,
    signal_mechanism: str,
    consumer: str,
    target_family: str,
    max_assistant_turns: int,
    ids: _Ids,
) -> Sample:
    lang = language.split("-")[0].lower()
    phrases = pack.phrases.get(lang, pack.phrases["en"])
    # Reviewer notes and the branch disclaimer stay in the system segment, never in trainable text.
    narrative = " ".join(phrases[item] for item in types)
    prompt = pack.prompts.get(lang, pack.prompts["en"])
    if cold:
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
            f"Synthetic {pack.sector} study. Language {language}.",
            f"Sub-domains: {', '.join(domains)}.",
            f"Consumer {consumer}. Target family {target_family}.",
            f"Reward {reward_mechanism}. Signal {signal_mechanism}.",
            reference,
            f"Generator {pack.generator_id}.",
            "Alternative branches are simulated, not causal counterfactuals.",
        )
    )
    if notes:
        system += " Notes: " + " | ".join(f"{note.stance} {note.target_type} {note.comment}" for note in notes)
    if revisions:
        system += " Revision notes: " + " | ".join(revisions)
    system = system[:2000]
    segments = [
        Segment(segment_id=ids.take("G"), role="system", text=system, trainable=False),
        Segment(segment_id=ids.take("G"), role="user", text=pack.user_line(kind, lang), trainable=False),
    ]
    segments.extend(
        Segment(segment_id=ids.take("G"), role="assistant", text=chunk, trainable=True)
        for chunk in _chunks(narrative, max(int(max_assistant_turns), 1))
    )
    return Sample(
        sample_id=ids.take("S"),
        prompt=prompt,
        sequences=[Sequence(sequence_id=ids.take("Q"), contexts=[Context(context_id=ids.take("X"), segments=segments)])],
    )


def _apply_rewards(built: list[_Journey], mechanism: str) -> None:
    if not built:
        return
    successes = [1.0 if item.success else 0.0 for item in built]
    lengths = [max(item.length, 1) for item in built]
    mean_success = sum(successes) / len(successes)
    median = statistics.median(lengths)
    for item, success, length in zip(built, successes, lengths):
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


def _chunks(text: str, turns: int) -> list[str]:
    words = text.split()
    if turns <= 1 or len(words) <= 12:
        return [text]
    size = max(1, (len(words) + turns - 1) // turns)
    chunks: list[str] = []
    for index in range(0, len(words), size):
        chunks.append(" ".join(words[index : index + size]))
        if len(chunks) == turns:
            rest = words[index + size :]
            if rest:
                chunks[-1] = f"{chunks[-1]} {' '.join(rest)}"
            break
    return chunks


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


def _money(pack: PackSpec, rng: random.Random, event_type: str, currency: str) -> tuple[float | None, str | None]:
    span = pack.amounts.get(event_type)
    if span is None:
        return None, None
    low, high = span
    return round(rng.uniform(low, high), 2), currency


def _rng(seed: str) -> random.Random:
    digest = hashlib.sha256(seed.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
