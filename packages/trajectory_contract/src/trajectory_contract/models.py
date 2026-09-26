from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ObservationStatus(str, Enum):
    observed = "observed"
    derived = "derived"
    imputed = "imputed"
    simulated = "simulated"


class ObjectRecord(BaseModel):
    object_id: str
    object_type: str
    subtype: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    source_system: str | None = None
    pii_class: str | None = None


class Relationship(BaseModel):
    relationship_id: str
    subject_id: str
    predicate: str
    object_id: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source: str | None = None
    confidence: float | None = None


class Event(BaseModel):
    event_id: str
    event_type: str
    event_time: datetime
    effective_time: datetime | None = None
    recorded_at: datetime | None = None
    timestamp_precision: Literal["second", "minute", "day", "month", "sequence_only", "unknown"] = "second"
    status: str | None = None
    amount: float | None = None
    currency: str | None = None
    # Seen from the customer's account: credit adds money, debit removes it.
    direction: Literal["debit", "credit"] | None = None
    amount_role: str | None = None
    channel_id: str | None = None
    case_id: str | None = None
    session_id: str | None = None
    source_system: str | None = None
    observation_status: ObservationStatus = ObservationStatus.simulated


class EventObject(BaseModel):
    event_id: str
    object_id: str
    object_role: str
    qualifier: str | None = None


class StateTransition(BaseModel):
    event_id: str
    object_id: str
    state_dimension: str
    state_before: str | None = None
    state_after: str | None = None
    reason: str | None = None
    confidence: float | None = None


class Trajectory(BaseModel):
    trajectory_id: str
    root_party_id: str
    trajectory_type: str
    start: datetime | None = None
    end: datetime | None = None
    observed_or_synthetic: Literal["observed", "synthetic", "alternative"]
    parent_trajectory_id: str | None = None
    branch_event_id: str | None = None
    generator_id: str | None = None
    probability: float | None = None
    # False on a simulated alternative: it makes no causal claim about what would have happened.
    causal_claim: bool | None = None
    # Trajectories drawn for the same prompt share a group id, the id of their sample.
    group_id: str | None = None
    event_ids: list[str]


class Segment(BaseModel):
    segment_id: str
    role: Literal["system", "user", "assistant", "tool"]
    text: str
    trainable: bool = False
    # A penalty rule that fired on this turn, and the advantage the turn carries after penalties.
    flagged_reason: str | None = None
    advantage: float | None = None


class Context(BaseModel):
    context_id: str
    segments: list[Segment]
    dropped: bool | None = None


class Sequence(BaseModel):
    sequence_id: str
    trajectory_id: str | None = None
    contexts: list[Context]
    reward: float | None = None
    advantage: float | None = None
    mask: list[int] | None = None
    outcome: Literal["pass", "fail"] | None = None
    solution_score: float | None = None
    behavior_score: float | None = None
    quality_factor: float | None = None
    token_estimate: int | None = None
    dropped: bool | None = None


class Sample(BaseModel):
    sample_id: str
    # The domain-layer trajectory this sample narrates.
    trajectory_id: str | None = None
    prompt: str
    sequences: list[Sequence]
    # The dynamic sampler rejects all-pass and all-fail groups; a group of one carries no group signal.
    group_accepted: bool | None = None
    group_pass_rate: float | None = None


class GenerationMeta(BaseModel):
    generator_id: str
    requested_trajectories: int
    primary_trajectories: int
    alternative_trajectories: int
    event_count: int
    limited_by: Literal["event_budget", "studio_cap", "acceptance"] | None = None
    # What the run's size counts and how far it got: prompts, or accepted groups, per share bucket.
    target: dict[str, Any] | None = None
    pack_version: str | None = None
    group_size: int | None = None
    rewards: dict[str, Any] | None = None
    steering: dict[str, Any] | None = None
    quality: dict[str, Any] | None = None
    # Variants and the process map, summarised for the studio.
    overview: dict[str, Any] | None = None
    # Where a large run's journeys live when they are not stored on the run.
    storage: dict[str, Any] | None = None
    # The notes and revision notes this run was generated from, and what each did.
    notes: dict[str, Any] | None = None


class TrajectoryBundle(BaseModel):
    objects: list[ObjectRecord]
    relationships: list[Relationship]
    events: list[Event]
    event_objects: list[EventObject]
    state_transitions: list[StateTransition]
    trajectories: list[Trajectory]
    samples: list[Sample] = Field(default_factory=list)
    generation: GenerationMeta | None = None
