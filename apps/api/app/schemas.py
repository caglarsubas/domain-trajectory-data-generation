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
    sector: Literal["banking", "insurance"] = "banking"


class CorpusLinkBody(BaseModel):
    kind: Literal["deep_search", "paper", "repo", "data_source", "ontology", "other"]
    name: str = Field(min_length=1, max_length=300)
    uri: str = Field(min_length=4, max_length=2000)


class RunBody(BaseModel):
    project_id: str
    sector: Literal["banking", "insurance"] = "banking"
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
    credential_id: str


class FeedbackBody(BaseModel):
    target_type: Literal["run", "trajectory", "event"]
    target_id: str = Field(min_length=1, max_length=64)
    stance: Literal["keep", "revise", "drop"]
    comment: str = Field(min_length=1, max_length=2000)


class RerunBody(BaseModel):
    feedback_ids: list[str] = Field(default_factory=list)
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
    credential_id: str | None = None


class DeepSearchBody(BaseModel):
    credential_id: str
    query: str | None = Field(default=None, max_length=2000)
    sub_domains: list[str] = Field(default_factory=list)
    language: str = Field(default="en", min_length=2, max_length=16)


class EvaluateBody(BaseModel):
    candidate: dict[str, Any] | None = None
