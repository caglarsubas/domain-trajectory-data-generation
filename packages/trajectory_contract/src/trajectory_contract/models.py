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
    # Every signal's verdict on this sequence: {signal: {"score", "passed", "items"}}; the run's signal sets `outcome`.
    signals: dict[str, dict[str, Any]] | None = None


class Sample(BaseModel):
    sample_id: str
    # The domain-layer trajectory this sample narrates.
    trajectory_id: str | None = None
    prompt: str
    sequences: list[Sequence]
    # The dynamic sampler rejects all-pass and all-fail groups; a group of one carries no group signal.
    group_accepted: bool | None = None
    group_pass_rate: float | None = None


class ToolSpec(BaseModel):
    """An operation of the mock bank, named after a BIAN service domain and action term."""

    name: str
    service_domain: str
    action: str
    description: str
    # JSON Schema for the arguments.
    parameters: dict[str, Any]
    # The pack events this operation records; several when an argument chooses the outcome.
    events: list[str]
    # The study's own API operation this tool follows, when an OpenAPI definition names one.
    http: dict[str, Any] | None = None


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class EpisodeTurn(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    text: str | None = None
    tool_call: ToolCall | None = None
    tool_result: dict[str, Any] | None = None
    trainable: bool = False


class RubricItem(BaseModel):
    item_id: str
    text: str
    kind: Literal["format", "legality", "grounding", "decision", "report"]


class Rollout(BaseModel):
    rollout_id: str
    # reference, alternative, perturbed:illegal, perturbed:wrong_object, or provider:<model>.
    policy: str
    turns: list[EpisodeTurn]
    action_event: str | None = None
    legal: bool
    outcome: Literal["pass", "fail"]
    rubric_scores: dict[str, float] = Field(default_factory=dict)
    reward: float | None = None
    advantage: float | None = None
    # For a provider rollout: how its call fared against the episode's skeleton.
    checks: dict[str, Any] | None = None


class Episode(BaseModel):
    """A decision in a journey as an agent task: the mock bank's state, its tools, a task, rubric items, and rollouts."""

    episode_id: str
    trajectory_id: str
    sample_id: str | None = None
    # How many events of the journey happened before the decision.
    decision_index: int
    state: dict[str, str]
    history: list[dict[str, Any]]
    task: str
    tools: list[ToolSpec]
    rubric: list[RubricItem]
    # What a provider turn is checked against: the legal actions, the case's objects, and the step the journey took.
    skeleton: dict[str, Any]
    rollouts: list[Rollout]


# Exported decision records carry this version; it changes whenever their fields change meaning.
DECISION_SCHEMA_VERSION = "decision-record/1"


class DecisionOption(BaseModel):
    """One outcome open at a decision point, with the generator's policy share and its simulated value."""

    option_id: str
    event_type: str
    label: str
    # The generator policy's share of this option among the decision's options.
    probability: float
    # The share of simulated continuations after this option, under the same policy, that reach the goal.
    value: float
    # The machine states that make the option legal, such as `application.application in {submitted}`.
    requires: list[str] = Field(default_factory=list)


class DecisionPoint(BaseModel):
    """An outcome decision in a journey: what was known, what was open, what the policy and the simulation say, and what happened."""

    decision_id: str
    trajectory_id: str
    sample_id: str | None = None
    # How many events of the journey happened before the decision, and the event that recorded it.
    decision_index: int
    decision_event_id: str
    # The pack's outcome group, such as application_decision, and the object kind it decides.
    group: str
    subject: str
    # Machine states before the decision (`kind.dimension` to state), and facts derived from the history.
    state: dict[str, str]
    facts: dict[str, Any]
    history: list[str]
    objects: dict[str, str]
    options: list[DecisionOption]
    # An outcome the state does not allow, for a true-or-false question with a false answer.
    distractor: dict[str, Any] | None = None
    taken: str
    # What the journey did after the decision: whether it reached the goal, its type, and its last event.
    outcome: dict[str, Any]
    value_samples: int
    goal: str
    # A decision-level counterfactual is relative to the generator's own policy, never a causal claim.
    counterfactual_basis: Literal["generator_policy"] = "generator_policy"


class DecisionRecord(BaseModel):
    """A typed question about a decision point, as exported for decision-scoring and Jev-type models."""

    schema_version: str = DECISION_SCHEMA_VERSION
    record_id: str
    decision_id: str
    trajectory_id: str
    sample_id: str | None = None
    language: str
    split: Literal["train", "calibration", "held_out"]
    # original, reordered (the same content with keys and options in another order), or paraphrase.
    variant: Literal["original", "reordered", "paraphrase"]
    variant_of: str | None = None
    question_type: Literal["choice", "score", "true_false"]
    question: str
    criteria: list[str]
    # The answers a model may give; the abstain answer is always allowed and never a target.
    options: list[dict[str, str]]
    abstain: dict[str, str]
    state: dict[str, str]
    facts: dict[str, Any]
    history: list[str]
    # A distribution over option ids for choice and true-or-false questions, a value in [0, 1] for score questions.
    target_distribution: dict[str, float] | None = None
    target_score: float | None = None
    # generator_policy_share, machine_rules, or simulated_goal_share.
    target_basis: str
    # Why the answer holds when a rule decides it, such as the unmet precondition of a false answer.
    rationale: str | None = None
    outcome: dict[str, Any]
    counterfactual_basis: Literal["generator_policy"] = "generator_policy"
    # The record as one prompt, for a language model.
    prompt: str


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
    # The jurisdiction profile the run was generated under: neutral, tr, or uk.
    jurisdiction: str | None = None
    # The data sources that calibrated next-step shares and durations, and how much they covered.
    calibration: dict[str, Any] | None = None
    # How many episodes were built, with how many rollouts, and how many were accepted as groups.
    episodes: dict[str, Any] | None = None
    # How many decision points were recorded, by outcome group, and how their values were simulated.
    decisions: dict[str, Any] | None = None


class TrajectoryBundle(BaseModel):
    objects: list[ObjectRecord]
    relationships: list[Relationship]
    events: list[Event]
    event_objects: list[EventObject]
    state_transitions: list[StateTransition]
    trajectories: list[Trajectory]
    samples: list[Sample] = Field(default_factory=list)
    episodes: list[Episode] = Field(default_factory=list)
    decisions: list[DecisionPoint] = Field(default_factory=list)
    generation: GenerationMeta | None = None
