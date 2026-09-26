from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

REWARD_MECHANISMS = (
    "binary_outcome",
    "groupwise_reward_synthesis",
    "groupwise_advantage_redistribution",
    "group_relative_length_penalty",
    "segment_penalty",
)
SIGNAL_MECHANISMS = (
    "outcome",
    "solution_rubric",
    "behavior_rubric",
    "process_conformance",
    "decision_score",
)
CONSUMERS = ("post_training", "decision_scoring", "evaluation")
FAMILIES = ("llm", "jev")


class RegisterBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    kind: Literal["user", "demo"] = "user"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class LoginBody(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class CredentialBody(BaseModel):
    provider: Literal["openai", "anthropic", "google", "xai"]
    label: str = Field(min_length=1, max_length=120)
    secret: str = Field(min_length=8, max_length=400)
    scope: Literal["platform", "byok"] = "byok"


class CredentialUpdateBody(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    secret: str | None = Field(default=None, min_length=8, max_length=400)

    @model_validator(mode="after")
    def _one_change(self) -> "CredentialUpdateBody":
        if self.label is None and self.secret is None:
            raise ValueError("send a new label, a new secret, or both")
        return self


class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # Any registered sector pack; an unknown one is refused with 422.
    sector: str = Field(default="banking", min_length=1, max_length=40)


class CorpusLinkBody(BaseModel):
    kind: Literal["deep_search", "paper", "repo", "data_source", "ontology", "other"]
    name: str = Field(min_length=1, max_length=300)
    uri: str = Field(min_length=4, max_length=2000)


class RunBody(BaseModel):
    project_id: str
    # Any registered sector pack; an unknown one is refused with 422.
    sector: str = Field(default="banking", min_length=1, max_length=40)
    target_trajectory_count: int = Field(ge=1, le=100_000)
    event_budget: int | None = Field(default=None, ge=1, le=5_000_000)
    min_events: int = Field(ge=1, le=10_000)
    max_events: int = Field(ge=1, le=10_000)
    max_assistant_turns: int = Field(ge=1, le=10_000)
    sub_domains: list[str] = Field(min_length=1)
    language: str = Field(min_length=2, max_length=16)
    start_mode: Literal["warm", "cold"]
    cold_start_acknowledged: bool = False
    reward_mechanism: Literal[
        "binary_outcome",
        "groupwise_reward_synthesis",
        "groupwise_advantage_redistribution",
        "group_relative_length_penalty",
        "segment_penalty",
    ]
    signal_mechanism: Literal[
        "outcome",
        "solution_rubric",
        "behavior_rubric",
        "process_conformance",
        "decision_score",
    ]
    consumer: Literal["post_training", "decision_scoring", "evaluation"]
    target_family: Literal["llm", "jev"]
    thresholds: dict[str, float] | None = None
    max_cycles: int = Field(default=2, ge=1, le=8)
    group_size: int = Field(default=1, ge=1, le=16)
    # "accepted_groups" counts only groups the dynamic sampler accepts; the job draws extra to reach it.
    target_kind: Literal["prompts", "accepted_groups"] = "prompts"
    # Relative shares per selected sub-domain; each share becomes its own bucket with its own target.
    domain_shares: dict[str, float] | None = None
    # Currency, product names, KYC rules, and default language: neutral retail, Turkey, or the United Kingdom.
    jurisdiction: Literal["neutral", "tr", "uk"] = "neutral"
    # Reweight next steps and durations from the study's data sources when it has any.
    calibrate: bool = True
    # Build agent episodes at decision points; unset means yes when the consumer is post-training or evaluation.
    episodes: bool | None = None
    # Record outcome decisions as typed questions; unset means yes for decision scoring or Jev-type targets.
    decisions: bool | None = None
    # Provider-model rollouts per episode through the run's key (two calls each), and a cap on the calls.
    provider_rollouts: int = Field(default=0, ge=0, le=4)
    provider_call_budget: int | None = Field(default=None, ge=2, le=4000)
    provider_model: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9._:-]*$")
    # Generation does not call a provider; a key is only needed for deep search.
    credential_id: str | None = None


class FeedbackBody(BaseModel):
    target_type: Literal["run", "trajectory", "event"]
    target_id: str = Field(min_length=1, max_length=64)
    stance: Literal["keep", "revise", "drop"]
    comment: str = Field(min_length=1, max_length=2000)


class RerunBody(BaseModel):
    feedback_ids: list[str] = Field(default_factory=list)
    jurisdiction: Literal["neutral", "tr", "uk"] | None = None
    calibrate: bool | None = None
    episodes: bool | None = None
    decisions: bool | None = None
    provider_rollouts: int | None = Field(default=None, ge=0, le=4)
    provider_call_budget: int | None = Field(default=None, ge=2, le=4000)
    provider_model: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9._:-]*$")
    target_trajectory_count: int | None = Field(default=None, ge=1, le=100_000)
    event_budget: int | None = None
    min_events: int | None = Field(default=None, ge=1, le=10_000)
    max_events: int | None = Field(default=None, ge=1, le=10_000)
    max_assistant_turns: int | None = Field(default=None, ge=1, le=10_000)
    sub_domains: list[str] | None = None
    language: str | None = None
    start_mode: Literal["warm", "cold"] | None = None
    cold_start_acknowledged: bool | None = None
    reward_mechanism: str | None = None
    signal_mechanism: str | None = None
    consumer: str | None = None
    target_family: str | None = None
    thresholds: dict[str, float] | None = None
    max_cycles: int | None = Field(default=None, ge=1, le=8)
    group_size: int | None = Field(default=None, ge=1, le=16)
    target_kind: Literal["prompts", "accepted_groups"] | None = None
    domain_shares: dict[str, float] | None = None
    credential_id: str | None = None


class CatalogueBody(BaseModel):
    entry: str = Field(min_length=2, max_length=64)


class MappingBody(BaseModel):
    # Activity name to event type, or null to leave an activity out.
    mapping: dict[str, str | None]


class FactReviewBody(BaseModel):
    key: str = Field(min_length=3, max_length=200)
    decision: Literal["accepted", "rejected", "clear"]


class ExportBody(BaseModel):
    held_out: str | None = None
    # Export a run the judge has not accepted; the manifest says so.
    allow_unaccepted: bool = False


class RegenerateBody(BaseModel):
    # The parent's notes to carry into the regenerated run; all of them when omitted.
    feedback_ids: list[str] | None = None


class DeepSearchBody(BaseModel):
    credential_id: str
    query: str | None = Field(default=None, max_length=2000)
    sub_domains: list[str] = Field(default_factory=list)
    language: str = Field(default="en", min_length=2, max_length=16)


class EvaluateBody(BaseModel):
    candidate: dict[str, Any] | None = None
