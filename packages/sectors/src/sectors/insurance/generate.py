"""Constrained semi-Markov generator for retail insurance journeys.

Hard order rules come from the insurance pack. Dwell times vary. An alternative
path is a simulated branch, not a causal counterfactual. The generator does not
call a provider.
"""

from __future__ import annotations

import hashlib
import random
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

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

from sectors.insurance.corpus import CorpusSteering, steering_from_text
from sectors.insurance.pack import EVENT_NAMESPACE

GENERATOR_ID = "insurance-semi-markov-v1"
STUDIO_TRAJECTORY_CAP = 64

CANON = (
    "product.viewed",
    "quote.started",
    "quote.submitted",
    "underwriting.started",
    "underwriting.referred",
    "underwriting.accepted",
    "underwriting.declined",
    "policy.bound",
    "policy.issued",
    "premium.paid",
    "claim.notified",
    "claim.assessed",
    "claim.settled",
    "claim.denied",
    "policy.renewed",
    "policy.cancelled",
    "complaint.received",
)

DWELL_HOURS = {
    "product.viewed": (0.0, 0.0),
    "quote.started": (0.05, 0.5),
    "quote.submitted": (0.1, 4.0),
    "underwriting.started": (0.05, 1.0),
    "underwriting.referred": (4.0, 72.0),
    "underwriting.accepted": (0.2, 48.0),
    "underwriting.declined": (0.2, 48.0),
    "policy.bound": (0.05, 6.0),
    "policy.issued": (0.2, 24.0),
    "premium.paid": (1.0, 72.0),
    "claim.notified": (24.0, 24.0 * 40),
    "claim.assessed": (24.0, 24.0 * 14),
    "claim.settled": (24.0, 24.0 * 21),
    "claim.denied": (4.0, 24.0 * 10),
    "policy.renewed": (24.0 * 300, 24.0 * 400),
    "policy.cancelled": (24.0, 24.0 * 60),
    "complaint.received": (24.0, 24.0 * 30),
}

REVISED_MIN_HOURS = 24.0 * 7

CASCADE = {
    "underwriting.accepted": (
        "policy.bound",
        "policy.issued",
        "premium.paid",
        "claim.notified",
        "claim.assessed",
        "claim.settled",
        "claim.denied",
        "policy.renewed",
        "policy.cancelled",
    ),
    "policy.issued": (
        "premium.paid",
        "claim.notified",
        "claim.assessed",
        "claim.settled",
        "claim.denied",
        "policy.renewed",
        "policy.cancelled",
    ),
    "claim.notified": ("claim.assessed", "claim.settled", "claim.denied"),
    "claim.assessed": ("claim.settled", "claim.denied"),
}

PREREQ = {
    "quote.submitted": ("quote.started",),
    "underwriting.started": ("quote.submitted",),
    "underwriting.referred": ("underwriting.started",),
    "underwriting.accepted": ("underwriting.started",),
    "underwriting.declined": ("underwriting.started",),
    "policy.bound": ("underwriting.accepted",),
    "policy.issued": ("policy.bound",),
    "premium.paid": ("policy.issued",),
    "claim.notified": ("policy.issued",),
    "claim.assessed": ("claim.notified",),
    "claim.settled": ("claim.assessed",),
    "claim.denied": ("claim.assessed",),
    "policy.renewed": ("policy.issued",),
    "policy.cancelled": ("policy.issued",),
    "complaint.received": (),
}

SYSTEM_EVENTS = {
    "underwriting.referred",
    "underwriting.accepted",
    "underwriting.declined",
    "policy.bound",
    "policy.issued",
    "claim.assessed",
    "claim.settled",
    "claim.denied",
    "policy.renewed",
    "policy.cancelled",
}

DEFAULT_CHANNEL = {
    "product.viewed": "web",
    "quote.started": "web",
    "quote.submitted": "web",
    "underwriting.started": "web",
    "premium.paid": "web",
    "claim.notified": "mobile",
    "complaint.received": "call_centre",
}

AMOUNTS = {
    "premium.paid": (40.0, 900.0),
    "claim.settled": (80.0, 8000.0),
}

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

VARIANT_TYPE = {
    "success": "policy_in_force",
    "referral": "underwriting_referral",
    "decline": "underwriting_declined",
    "claim": "claim_settled",
    "denied": "claim_denied",
    "renewal": "policy_renewed",
    "cancel": "policy_cancelled",
    "complaint": "complaint_case",
}
TYPE_TO_VARIANT = {value: key for key, value in VARIANT_TYPE.items()}

DOMAIN_EVENTS = {
    "quoting": {"product.viewed", "quote.started", "quote.submitted"},
    "underwriting": {
        "underwriting.started",
        "underwriting.referred",
        "underwriting.accepted",
        "underwriting.declined",
    },
    "policy_administration": {"policy.bound", "policy.issued", "policy.renewed", "policy.cancelled"},
    "billing": {"premium.paid"},
    "claims": {"claim.notified", "claim.assessed", "claim.settled", "claim.denied"},
    "servicing": {"policy.renewed", "complaint.received"},
    "complaints": {"complaint.received"},
}

OBJECTS = {
    "party": ("P", "party", "individual"),
    "offering": ("F", "product_offering", "motor"),
    "quote": ("U", "quote", "new_business"),
    "policy": ("Y", "policy", "motor"),
    "claim": ("H", "claim", "loss"),
    "complaint": ("M", "complaint", "service"),
}

ROLES = {
    "product.viewed": (("party", "prospect"), ("offering", "offering")),
    "quote.started": (("party", "applicant"), ("quote", "quote")),
    "quote.submitted": (("party", "applicant"), ("quote", "quote")),
    "underwriting.started": (("party", "applicant"), ("quote", "quote")),
    "underwriting.referred": (("party", "applicant"), ("quote", "quote")),
    "underwriting.accepted": (("party", "applicant"), ("quote", "quote")),
    "underwriting.declined": (("party", "applicant"), ("quote", "quote")),
    "policy.bound": (("party", "policyholder"), ("policy", "policy"), ("quote", "quote")),
    "policy.issued": (("party", "policyholder"), ("policy", "policy")),
    "premium.paid": (("party", "policyholder"), ("policy", "policy")),
    "claim.notified": (("party", "claimant"), ("claim", "claim"), ("policy", "policy")),
    "claim.assessed": (("claim", "claim"), ("policy", "policy")),
    "claim.settled": (("claim", "claim"), ("policy", "policy")),
    "claim.denied": (("claim", "claim"), ("policy", "policy")),
    "policy.renewed": (("party", "policyholder"), ("policy", "policy")),
    "policy.cancelled": (("party", "policyholder"), ("policy", "policy")),
    "complaint.received": (("party", "complainant"), ("complaint", "case")),
}

EN = {
    "product.viewed": "The prospect viewed a retail cover.",
    "quote.started": "A quote was started.",
    "quote.submitted": "The quote was submitted.",
    "underwriting.started": "Underwriting started.",
    "underwriting.referred": "Underwriting was referred.",
    "underwriting.accepted": "Underwriting accepted the risk.",
    "underwriting.declined": "Underwriting declined the risk.",
    "policy.bound": "The policy was bound.",
    "policy.issued": "The policy was issued.",
    "premium.paid": "The premium was paid.",
    "claim.notified": "A claim was notified.",
    "claim.assessed": "The claim was assessed.",
    "claim.settled": "The claim was settled.",
    "claim.denied": "The claim was denied.",
    "policy.renewed": "The policy was renewed.",
    "policy.cancelled": "The policy was cancelled.",
    "complaint.received": "A complaint was received.",
}
TR = {
    "product.viewed": "Aday bir teminat inceledi.",
    "quote.started": "Teklif başladı.",
    "quote.submitted": "Teklif iletildi.",
    "underwriting.started": "Risk değerlendirmesi başladı.",
    "underwriting.referred": "Risk değerlendirmesi incelemeye alındı.",
    "underwriting.accepted": "Risk kabul edildi.",
    "underwriting.declined": "Risk reddedildi.",
    "policy.bound": "Poliçe bağlandı.",
    "policy.issued": "Poliçe düzenlendi.",
    "premium.paid": "Prim ödendi.",
    "claim.notified": "Hasar ihbarı yapıldı.",
    "claim.assessed": "Hasar incelendi.",
    "claim.settled": "Hasar ödendi.",
    "claim.denied": "Hasar reddedildi.",
    "policy.renewed": "Poliçe yenilendi.",
    "policy.cancelled": "Poliçe iptal edildi.",
    "complaint.received": "Şikayet kaydı açıldı.",
}


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


def generate_insurance_bundle(
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
    seed: str = "insurance",
    materialization_cap: int = STUDIO_TRAJECTORY_CAP,
) -> TrajectoryBundle:
    domains = [name for name in sub_domains if name in DOMAIN_EVENTS] or ["quoting"]
    cold = start_mode == "cold"
    steering = CorpusSteering(None, None, (), ()) if cold else steering_from_text(corpus_text or "")
    notes = _coerce_notes(feedback)
    revisions = [item for item in (revision_notes or []) if item]
    event_index, trajectory_index = _parent_index(parent_bundle)
    dropped_types, kept_types, revised, dropped_variants, kept_variants = _interpret(notes, event_index, trajectory_index)
    allowed: set[str] = set()
    for domain in domains:
        allowed.update(DOMAIN_EVENTS[domain])
    blocked = _expand_drops(dropped_types)
    for event_type in steering.events:
        if event_type in allowed and event_type not in blocked and event_type not in kept_types:
            kept_types.append(event_type)
    enrich = any("helpfulness" in item.lower() for item in revisions)
    suppress_denied = any("correctness" in item.lower() for item in revisions)
    variants = _variants(set(domains), suppress_denied, dropped_variants, kept_variants)
    rng = _rng(seed)
    requested = max(int(target_trajectory_count), 1)
    limit = min(requested, max(int(materialization_cap), 1))
    floor, cap = _bounds(min_events, max_events)
    remaining = None if event_budget is None else max(int(event_budget), 1)
    ids = _Ids()
    built: list[_Journey] = []
    limited_by: str | None = None
    objects: list[ObjectRecord] = []
    relationships: list[Relationship] = []
    events: list[Event] = []
    links: list[EventObject] = []
    transitions: list[StateTransition] = []

    while len(built) < limit:
        variant = variants[len(built) % len(variants)]
        room = remaining
        sequence = _fit(variant, set(domains), floor, cap, room, dropped_types, kept_types, enrich=False)
        if room is not None and len(sequence) > room:
            if built:
                limited_by = "event_budget"
                break
            sequence = _fit(variant, set(domains), 1, cap, room, dropped_types, kept_types, enrich=False)
        alt = _alternative(sequence, dropped_types)
        extra = _extra(sequence, alt)
        if room is not None and len(sequence) + extra > room:
            alt = None
            extra = 0
        if enrich and len(sequence) < cap and (room is None or len(sequence) + extra < room):
            longer = _fit(variant, set(domains), floor, cap, room, dropped_types, kept_types, enrich=True)
            longer_alt = _alternative(longer, dropped_types)
            longer_extra = _extra(longer, longer_alt)
            if room is None or len(longer) + longer_extra <= room:
                sequence, alt, extra = longer, longer_alt, longer_extra
                if room is not None and len(sequence) + extra > room:
                    alt, extra = None, 0
        journey = _materialize(
            sequence=sequence,
            alt=alt,
            variant=variant,
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
        objects.extend(journey.objects)
        relationships.extend(journey.relationships)
        events.extend(journey.events)
        links.extend(journey.links)
        transitions.extend(journey.transitions)
        if remaining is not None:
            remaining -= len(sequence) + extra
            if remaining <= 0:
                limited_by = "event_budget"
                break

    if limited_by is None and len(built) < requested:
        limited_by = "studio_cap"
    _apply_rewards(built, reward_mechanism)
    return TrajectoryBundle(
        objects=objects,
        relationships=relationships,
        events=events,
        event_objects=links,
        state_transitions=transitions,
        trajectories=[item for journey in built for item in journey.trajectories],
        samples=[journey.sample for journey in built],
        generation=GenerationMeta(
            generator_id=GENERATOR_ID,
            requested_trajectories=requested,
            primary_trajectories=len(built),
            alternative_trajectories=sum(1 for journey in built if len(journey.trajectories) > 1),
            event_count=len(events),
            limited_by=limited_by,
        ),
    )


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


def _materialize(
    *,
    sequence: list[str],
    alt: tuple[list[str], str] | None,
    variant: str,
    index: int,
    domains: list[str],
    language: str,
    steering: CorpusSteering,
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
    primary, _ = _emit(
        sequence,
        start=start,
        advance_first=False,
        catalog=catalog,
        state=state,
        snapshots=snapshots,
        steering=steering,
        currency=currency,
        revised=revised,
        rng=rng,
        ids=ids,
        journey=journey,
    )
    root = catalog["party"].object_id
    opened = primary[0].event_time
    closed = primary[-1].event_time
    trajectory_id = ids.take("T")
    journey.trajectories.append(
        Trajectory(
            trajectory_id=trajectory_id,
            root_party_id=root,
            trajectory_type=VARIANT_TYPE[variant],
            start=opened,
            end=closed,
            observed_or_synthetic="synthetic",
            generator_id=GENERATOR_ID,
            probability=1.0,
            event_ids=[item.event_id for item in primary],
        )
    )
    suffix: list[str] = []
    branch_event = primary[0]
    if alt is not None:
        alt_seq, branch_type = alt
        branch_index = sequence.index(branch_type)
        branch_event = primary[branch_index]
        suffix = alt_seq[branch_index + 1 :]
        alt_state = dict(snapshots[branch_event.event_id])
    if suffix:
        alt_events, _ = _emit(
            suffix,
            start=branch_event.event_time,
            advance_first=True,
            catalog=catalog,
            state=alt_state,
            snapshots=snapshots,
            steering=steering,
            currency=currency,
            revised=revised,
            rng=rng,
            ids=ids,
            journey=journey,
        )
        alt_ids = [item.event_id for item in primary[: branch_index + 1]] + [item.event_id for item in alt_events]
        journey.trajectories.append(
            Trajectory(
                trajectory_id=f"{trajectory_id}A",
                root_party_id=root,
                trajectory_type=VARIANT_TYPE[variant],
                start=opened,
                end=alt_events[-1].event_time if alt_events else branch_event.event_time,
                observed_or_synthetic="alternative",
                parent_trajectory_id=trajectory_id,
                branch_event_id=branch_event.event_id,
                generator_id=GENERATOR_ID,
                probability=round(rng.uniform(0.18, 0.42), 2),
                event_ids=alt_ids,
            )
        )
    _relate(catalog, opened, ids, journey)
    journey.sample = _sample(
        trajectory_id=trajectory_id,
        sequence=sequence,
        variant=variant,
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
        has_alternative=alt is not None,
        ids=ids,
    )
    types = set(sequence)
    covered = sum(1 for domain in domains if types & DOMAIN_EVENTS[domain])
    journey.success = _success(sequence)
    journey.length = len(sequence)
    journey.coverage = covered / len(domains)
    return journey


def _emit(
    types: list[str],
    *,
    start: datetime,
    advance_first: bool,
    catalog: dict[str, ObjectRecord],
    state: dict[tuple[str, str], str],
    snapshots: dict[str, dict[tuple[str, str], str]],
    steering: CorpusSteering,
    currency: str,
    revised: dict[str, str],
    rng: random.Random,
    ids: _Ids,
    journey: _Journey,
) -> tuple[list[Event], datetime]:
    cursor = start
    emitted: list[Event] = []
    for index, event_type in enumerate(types):
        if index > 0 or advance_first:
            cursor = _step(cursor, event_type, rng, revised)
        for kind, _role in ROLES[event_type]:
            _ensure(kind, cursor, catalog, steering, ids, journey)
        amount, unit = _money(rng, event_type, currency)
        event = Event(
            event_id=ids.take("E"),
            event_type=event_type,
            event_time=cursor,
            effective_time=cursor,
            recorded_at=cursor + timedelta(seconds=2),
            timestamp_precision="second",
            status="simulated",
            amount=amount,
            currency=unit,
            channel_id=_channel(event_type, steering.channel),
            case_id=catalog["party"].object_id.replace("P", "C", 1),
            session_id=catalog["party"].object_id.replace("P", "S", 1),
            source_system=GENERATOR_ID,
            observation_status=ObservationStatus.simulated,
        )
        journey.events.append(event)
        for kind, role in ROLES[event_type]:
            journey.links.append(EventObject(event_id=event.event_id, object_id=catalog[kind].object_id, object_role=role))
        _apply_state(event_type, event.event_id, catalog, state, journey.transitions)
        snapshots[event.event_id] = dict(state)
        emitted.append(event)
    return emitted, cursor


def _ensure(
    kind: str,
    when: datetime,
    catalog: dict[str, ObjectRecord],
    steering: CorpusSteering,
    ids: _Ids,
    journey: _Journey,
) -> ObjectRecord:
    existing = catalog.get(kind)
    if existing is not None:
        return existing
    prefix, object_type, subtype = OBJECTS[kind]
    if kind in {"offering", "policy"} and steering.products:
        subtype = steering.products[0]
    attributes: dict[str, Any] = {"synthetic": True}
    if kind == "offering" and steering.terms:
        attributes["corpus_terms"] = list(steering.terms)
    record = ObjectRecord(
        object_id=ids.take(prefix),
        object_type=object_type,
        subtype=subtype,
        valid_from=when,
        attributes=attributes,
        source_system=GENERATOR_ID,
        pii_class="synthetic" if kind == "party" else "none",
    )
    catalog[kind] = record
    journey.objects.append(record)
    return record


def _apply_state(
    event_type: str,
    event_id: str,
    catalog: dict[str, ObjectRecord],
    state: dict[tuple[str, str], str],
    transitions: list[StateTransition],
) -> None:
    def put(kind: str, dimension: str, after: str) -> None:
        obj = catalog.get(kind)
        if obj is None:
            return
        key = (obj.object_id, dimension)
        current = state.get(key)
        if current == after:
            return
        state[key] = after
        transitions.append(
            StateTransition(
                event_id=event_id,
                object_id=obj.object_id,
                state_dimension=dimension,
                state_before=current,
                state_after=after,
                reason=event_type,
                confidence=1.0,
            )
        )

    if event_type == "quote.started":
        put("quote", "quote", "started")
    elif event_type == "quote.submitted":
        put("quote", "quote", "submitted")
    elif event_type == "underwriting.started":
        put("quote", "underwriting", "pending")
    elif event_type == "underwriting.referred":
        put("quote", "underwriting", "referred")
    elif event_type == "underwriting.accepted":
        put("quote", "underwriting", "accepted")
    elif event_type == "underwriting.declined":
        put("quote", "underwriting", "declined")
    elif event_type == "policy.bound":
        put("policy", "policy", "bound")
        put("party", "relationship", "policyholder")
    elif event_type == "policy.issued":
        put("policy", "policy", "in_force")
    elif event_type == "premium.paid":
        put("policy", "billing", "paid")
    elif event_type == "claim.notified":
        put("claim", "claim", "open")
    elif event_type == "claim.assessed":
        put("claim", "claim", "assessed")
    elif event_type == "claim.settled":
        put("claim", "claim", "settled")
    elif event_type == "claim.denied":
        put("claim", "claim", "denied")
    elif event_type == "policy.renewed":
        put("policy", "policy", "renewed")
    elif event_type == "policy.cancelled":
        put("policy", "policy", "cancelled")
    elif event_type == "complaint.received":
        put("complaint", "relationship", "complaint_open")


def _relate(catalog: dict[str, ObjectRecord], when: datetime, ids: _Ids, journey: _Journey) -> None:
    pairs = (
        ("party", "REQUESTED", "quote"),
        ("quote", "RESULTED_IN", "policy"),
        ("party", "HOLDS", "policy"),
        ("claim", "AGAINST", "policy"),
        ("party", "NOTIFIED", "claim"),
        ("party", "RAISED", "complaint"),
    )
    for subject, predicate, target in pairs:
        if subject in catalog and target in catalog:
            journey.relationships.append(
                Relationship(
                    relationship_id=ids.take("R"),
                    subject_id=catalog[subject].object_id,
                    predicate=predicate,
                    object_id=catalog[target].object_id,
                    valid_from=when,
                    source=GENERATOR_ID,
                    confidence=1.0,
                )
            )


def _sample(
    *,
    trajectory_id: str,
    sequence: list[str],
    variant: str,
    domains: list[str],
    language: str,
    steering: CorpusSteering,
    cold: bool,
    revised: dict[str, str],
    notes: list[Note],
    revisions: list[str],
    reward_mechanism: str,
    signal_mechanism: str,
    consumer: str,
    target_family: str,
    max_assistant_turns: int,
    has_alternative: bool,
    ids: _Ids,
) -> Sample:
    del trajectory_id
    lang = language.split("-")[0].lower()
    phrases = TR if lang == "tr" else EN
    narrative = " ".join(phrases[item] for item in sequence)
    if revised:
        narrative += " " + " ".join(f"Revision requested for {name}: {text}" for name, text in revised.items())
    if has_alternative:
        narrative += " A simulated alternative branches from this journey. It is not a causal counterfactual."
    user = _user_line(variant, lang)
    prompt = (
        "Sentetik bir perakende sigorta yolculuğu üret. Gerçek kişi, poliçe veya hasar numarası uydurma."
        if lang == "tr"
        else "Simulate a synthetic retail-insurance journey. Do not invent real people, policy numbers, or claim references."
    )
    if cold:
        reference = "Cold start. No warm-start corpus was used."
    else:
        parts = []
        if steering.terms:
            parts.append("Warm corpus terms: " + ", ".join(steering.terms) + ".")
        if steering.events:
            parts.append("Corpus events: " + ", ".join(steering.events) + ".")
        reference = " ".join(parts) or "Warm corpus attached. No insurance terms matched."
    system = " ".join(
        (
            f"Synthetic insurance study. Language {language}.",
            f"Sub-domains: {', '.join(domains)}.",
            f"Consumer {consumer}. Target family {target_family}.",
            f"Reward {reward_mechanism}. Signal {signal_mechanism}.",
            reference,
            f"Generator {GENERATOR_ID}.",
            "Alternative branches are simulated, not causal counterfactuals.",
        )
    )
    if notes:
        system += " Notes: " + " | ".join(f"{note.stance} {note.target_type} {note.comment}" for note in notes)
    if revisions:
        system += " Revision notes: " + " | ".join(revisions)
    turns = max(int(max_assistant_turns), 1)
    segments = [
        Segment(segment_id=ids.take("G"), role="system", text=system[:2000], trainable=False),
        Segment(segment_id=ids.take("G"), role="user", text=user, trainable=False),
    ]
    segments.extend(
        Segment(segment_id=ids.take("G"), role="assistant", text=chunk, trainable=True)
        for chunk in _chunks(narrative, turns)
    )
    return Sample(
        sample_id=ids.take("S"),
        prompt=prompt,
        sequences=[
            Sequence(
                sequence_id=ids.take("Q"),
                contexts=[Context(context_id=ids.take("X"), segments=segments)],
            )
        ],
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


def _user_line(variant: str, lang: str) -> str:
    if lang == "tr":
        if variant in {"claim", "denied"}:
            return "Hasar bildirimi yapmak istiyorum."
        if variant == "complaint":
            return "Bir şikayet iletmek istiyorum."
        return "Sigorta teklifi istiyorum."
    if variant in {"claim", "denied"}:
        return "I need to claim on my policy."
    if variant == "complaint":
        return "I want to raise a complaint."
    return "I want a quote for cover."


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


def _fit(
    variant: str,
    domains: set[str],
    floor: int,
    cap: int,
    budget: int | None,
    dropped: set[str],
    kept: list[str],
    enrich: bool,
) -> list[str]:
    limit = cap if budget is None else min(cap, max(budget, 1))
    low = min(floor, limit)
    sequence = _repair(_without(_base(variant, domains), dropped))
    if len(sequence) > limit:
        sequence = _shrink(sequence, limit)
    if len(sequence) < low:
        sequence = _pad(sequence, domains, low, dropped)
        if len(sequence) > limit:
            sequence = _shrink(sequence, limit)
    sequence = _ensure_kept(sequence, kept, limit, dropped)
    sequence = _repair(sequence)
    if enrich and len(sequence) < limit:
        sequence = _pad(sequence, domains, len(sequence) + 1, dropped)
        sequence = _repair(sequence)
        if len(sequence) > limit:
            sequence = _shrink(sequence, limit)
    return sequence or ["product.viewed"]


def _base(variant: str, domains: set[str]) -> list[str]:
    if variant == "complaint" and domains <= {"complaints"}:
        return ["complaint.received"]
    sequence = ["product.viewed", "quote.started", "quote.submitted", "underwriting.started"]
    if variant == "referral":
        sequence.extend(["underwriting.referred", "underwriting.accepted"])
    elif variant == "decline":
        sequence.append("underwriting.declined")
        return sequence
    else:
        sequence.append("underwriting.accepted")
    if _needs_policy(domains, variant):
        sequence.extend(["policy.bound", "policy.issued"])
    if "billing" in domains or variant in {"claim", "denied", "renewal", "cancel"}:
        if "policy.issued" in sequence:
            sequence.append("premium.paid")
    if variant == "claim":
        sequence.extend(["claim.notified", "claim.assessed", "claim.settled"])
    elif variant == "denied":
        sequence.extend(["claim.notified", "claim.assessed", "claim.denied"])
    elif variant == "renewal" or (variant == "success" and "servicing" in domains and "policy.issued" in sequence):
        sequence.append("policy.renewed")
    elif variant == "cancel":
        sequence.append("policy.cancelled")
    if variant == "complaint" or (variant == "success" and "complaints" in domains):
        sequence.append("complaint.received")
    return sequence


def _needs_policy(domains: set[str], variant: str) -> bool:
    if variant == "decline":
        return False
    return bool(domains & {"policy_administration", "billing", "claims", "servicing", "complaints"}) or variant in {
        "claim",
        "denied",
        "renewal",
        "cancel",
    }


def _without(sequence: list[str], dropped: set[str]) -> list[str]:
    blocked = _expand_drops(dropped)
    return [item for item in sequence if item not in blocked]


def _expand_drops(dropped: set[str]) -> set[str]:
    blocked = set(dropped)
    changed = True
    while changed:
        changed = False
        for source, deps in CASCADE.items():
            if source in blocked:
                for dep in deps:
                    if dep not in blocked:
                        blocked.add(dep)
                        changed = True
    return blocked


def _repair(sequence: list[str]) -> list[str]:
    current = list(sequence)
    changed = True
    while changed:
        changed = False

        def drop(types: set[str]) -> None:
            nonlocal current, changed
            nxt = [item for item in current if item not in types]
            if nxt != current:
                current = nxt
                changed = True

        if "quote.started" not in current:
            drop({"quote.submitted", "underwriting.started", "underwriting.referred", "underwriting.accepted", "underwriting.declined"})
        if "quote.submitted" not in current:
            drop({"underwriting.started", "underwriting.referred", "underwriting.accepted", "underwriting.declined"})
        if "underwriting.started" not in current:
            drop({"underwriting.referred", "underwriting.accepted", "underwriting.declined"})
        if "underwriting.accepted" not in current:
            drop(
                {
                    "policy.bound",
                    "policy.issued",
                    "premium.paid",
                    "claim.notified",
                    "claim.assessed",
                    "claim.settled",
                    "claim.denied",
                    "policy.renewed",
                    "policy.cancelled",
                }
            )
        if "policy.bound" not in current:
            drop({"policy.issued", "premium.paid", "claim.notified", "claim.assessed", "claim.settled", "claim.denied", "policy.renewed", "policy.cancelled"})
        if "policy.issued" not in current:
            drop({"premium.paid", "claim.notified", "claim.assessed", "claim.settled", "claim.denied", "policy.renewed", "policy.cancelled"})
        if "claim.notified" not in current:
            drop({"claim.assessed", "claim.settled", "claim.denied"})
        if "claim.assessed" not in current:
            drop({"claim.settled", "claim.denied"})
    return current


def _shrink(sequence: list[str], cap: int) -> list[str]:
    current = list(sequence)
    while len(current) > cap and current:
        current.pop()
        current = _repair(current)
    return current


def _pad(sequence: list[str], domains: set[str], target: int, dropped: set[str]) -> list[str]:
    current = list(sequence)
    blocked = _expand_drops(dropped)
    guard = 0
    while len(current) < target and guard < target + 8:
        guard += 1
        before = len(current)
        if (
            "underwriting.accepted" in current
            and "underwriting.referred" not in current
            and "underwriting.referred" not in blocked
        ):
            accepted_at = current.index("underwriting.accepted")
            current.insert(accepted_at, "underwriting.referred")
        elif "policy.issued" in current and current.count("premium.paid") < 3 and "premium.paid" not in blocked:
            current.append("premium.paid")
        elif "claim.assessed" in current and current.count("claim.settled") < 2 and "claim.settled" not in blocked:
            current.append("claim.settled")
        elif domains & {"complaints", "servicing"} and current.count("complaint.received") < 3 and "complaint.received" not in blocked:
            current.append("complaint.received")
        else:
            current.append("product.viewed")
        if len(current) == before:
            break
    return current


def _ensure_kept(sequence: list[str], kept: list[str], cap: int, dropped: set[str]) -> list[str]:
    current = list(sequence)
    blocked = _expand_drops(dropped)
    for event_type in kept:
        if event_type not in CANON or event_type in current or event_type in blocked:
            continue
        needed: list[str] = []

        def walk(item: str) -> None:
            for pre in PREREQ.get(item, ()):
                walk(pre)
            if item not in current and item not in needed:
                needed.append(item)

        walk(event_type)
        if any(item in blocked for item in needed):
            continue
        if len(current) + len(needed) > cap:
            continue
        for item in needed:
            current = _insert_ordered(current, item)
    return current


def _insert_ordered(sequence: list[str], event_type: str) -> list[str]:
    rank = CANON.index(event_type)
    index = len(sequence)
    for cursor, existing in enumerate(sequence):
        if CANON.index(existing) > rank:
            index = cursor
            break
    nxt = list(sequence)
    nxt.insert(index, event_type)
    return nxt


def _alternative(sequence: list[str], dropped: set[str]) -> tuple[list[str], str] | None:
    blocked = _expand_drops(dropped)
    if (
        "underwriting.started" in sequence
        and "underwriting.accepted" in sequence
        and "underwriting.referred" not in sequence
        and "underwriting.referred" not in blocked
    ):
        index = sequence.index("underwriting.started")
        alt = sequence[: index + 1] + ["underwriting.referred", "underwriting.accepted"]
        alt = _repair(_without(alt, dropped))
        if "underwriting.referred" in alt and alt[: index + 1] == sequence[: index + 1]:
            return alt, sequence[index]
    if "underwriting.accepted" in sequence and "underwriting.declined" not in blocked:
        index = sequence.index("underwriting.accepted")
        if index > 0:
            alt = sequence[:index] + ["underwriting.declined"]
            if alt[:index] == sequence[:index]:
                return alt, sequence[index - 1]
    return None


def _extra(sequence: list[str], alt: tuple[list[str], str] | None) -> int:
    if alt is None:
        return 0
    alt_seq, branch_type = alt
    return max(0, len(alt_seq) - (sequence.index(branch_type) + 1))


def _variants(domains: set[str], suppress_denied: bool, dropped: set[str], kept: set[str]) -> list[str]:
    if domains <= {"complaints"}:
        found = ["complaint"]
    else:
        found = ["success"]
        if "underwriting" in domains:
            found.extend(["referral", "decline"])
        if "claims" in domains:
            found.append("claim")
            if not suppress_denied:
                found.append("denied")
        if domains & {"policy_administration", "servicing"}:
            found.append("renewal")
        if "policy_administration" in domains:
            found.append("cancel")
        if domains & {"complaints", "servicing"}:
            found.append("complaint")
    found = [item for item in found if item not in dropped]
    if kept:
        narrowed = [item for item in found if item in kept]
        found = narrowed or found
    return found or ["success"]


def _interpret(
    notes: list[Note],
    event_index: dict[str, str],
    trajectory_index: dict[str, str],
) -> tuple[set[str], list[str], dict[str, str], set[str], set[str]]:
    dropped: set[str] = set()
    kept: list[str] = []
    revised: dict[str, str] = {}
    dropped_variants: set[str] = set()
    kept_variants: set[str] = set()
    known = set(EVENT_NAMESPACE)
    for note in notes:
        if note.target_type == "event":
            event_type = event_index.get(note.target_id)
            if event_type is None and note.target_id in known:
                event_type = note.target_id
            if event_type is None:
                continue
            if note.stance == "drop":
                dropped.add(event_type)
            elif note.stance == "keep":
                kept.append(event_type)
            elif note.stance == "revise":
                revised[event_type] = note.comment
        elif note.target_type == "trajectory":
            trajectory_type = trajectory_index.get(note.target_id, note.target_id)
            variant = TYPE_TO_VARIANT.get(trajectory_type)
            if variant is None:
                continue
            if note.stance == "drop":
                dropped_variants.add(variant)
            elif note.stance == "keep":
                kept_variants.add(variant)
    dropped -= set(kept)
    return dropped, kept, revised, dropped_variants, kept_variants


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
        else:
            notes.append(Note(item["target_type"], item["target_id"], item["stance"], item["comment"]))
    return notes


def _bounds(min_events: int, max_events: int) -> tuple[int, int]:
    high = max(int(max_events), 1)
    low = max(int(min_events), 1)
    if low > high:
        low = high
    return low, high


def _step(cursor: datetime, event_type: str, rng: random.Random, revised: dict[str, str]) -> datetime:
    low, high = DWELL_HOURS[event_type]
    if event_type in revised:
        low = max(low, REVISED_MIN_HOURS)
        high = max(high, low)
    hours = rng.uniform(low, high)
    delta = timedelta(hours=hours)
    if delta < timedelta(seconds=1):
        delta = timedelta(seconds=1)
    return cursor + delta


def _channel(event_type: str, preferred: str | None) -> str:
    if event_type in SYSTEM_EVENTS:
        return "system"
    if preferred:
        return preferred
    return DEFAULT_CHANNEL.get(event_type, "web")


def _money(rng: random.Random, event_type: str, currency: str) -> tuple[float | None, str | None]:
    span = AMOUNTS.get(event_type)
    if span is None:
        return None, None
    low, high = span
    return round(rng.uniform(low, high), 2), currency


def _success(sequence: list[str]) -> bool:
    if not sequence:
        return False
    if sequence[-1] in {"underwriting.declined", "claim.denied", "policy.cancelled"}:
        return False
    if "underwriting.declined" in sequence or "claim.denied" in sequence:
        return False
    return True


def _rng(seed: str) -> random.Random:
    digest = hashlib.sha256(seed.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
