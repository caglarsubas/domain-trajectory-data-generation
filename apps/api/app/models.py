from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import Base


def _id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Credential(Base):
    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(120))
    ciphertext: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(16))
    ready: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # The last live check against the provider: valid, rejected, or unreachable.
    check_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    check_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    sector: Mapped[str] = mapped_column(String(32), default="banking")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # A person's decisions on extracted facts: fact key to "accepted" or "rejected".
    fact_reviews: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    corpus_items: Mapped[list[CorpusItem]] = relationship(back_populates="project")


class CorpusItem(Base):
    __tablename__ = "corpus_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(300))
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(String(64), default="upload")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # How a link was fetched: status, final address, content type, size, and when, or why it failed.
    ingest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    project: Mapped[Project] = relationship(back_populates="corpus_items")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    parent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="stubbed")
    config: Mapped[dict] = mapped_column(JSON)
    inherited_feedback_ids: Mapped[list] = mapped_column(JSON, default=list)
    candidate: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # A copy of the candidate's generation metadata, so listing runs never loads a whole bundle.
    generation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(64))
    stance: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EvalCycle(Base):
    __tablename__ = "eval_cycles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    cycle_index: Mapped[int] = mapped_column(Integer)
    hard_check_passed: Mapped[int] = mapped_column(Integer)
    hard_check_errors: Mapped[list] = mapped_column(JSON, default=list)
    reference_quality: Mapped[str] = mapped_column(String(16))
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    revision_notes: Mapped[list] = mapped_column(JSON, default=list)
    judge_tenant: Mapped[str] = mapped_column(String(120), default="")
    judge_org_id: Mapped[str] = mapped_column(String(120), default="")
    judge_key_id: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # The journeys judged, the models asked, per-rubric scores per model, agreement, and audit flags.
    sample: Mapped[list | None] = mapped_column(JSON, nullable=True)
    models: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agreement: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    flags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    canary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The warm-start passages the judge read, by source and passage number.
    reference: Mapped[list | None] = mapped_column(JSON, nullable=True)


class EvalVerdict(Base):
    __tablename__ = "eval_verdicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("eval_cycles.id"), index=True)
    rubric: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column()
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[str] = mapped_column(Text, default="")
    judge_model: Mapped[str] = mapped_column(String(200), default="")
    duration_ms: Mapped[float] = mapped_column(default=0)
    # Which journey, which order for a pairwise call ("ab" or "ba"), and whether it was the control journey.
    trajectory_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pair_order: Mapped[str | None] = mapped_column(String(4), nullable=True)
    canary: Mapped[int] = mapped_column(Integer, default=0)


class Job(Base):
    """Work that outlives a request: generating, exporting, or judging a run, or a deep search for a study."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    kind: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True, nullable=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[float] = mapped_column(default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the handler produced, such as the corpus item a deep search stored, or the HTTP status of a refusal.
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
