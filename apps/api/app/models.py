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


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    sector: Mapped[str] = mapped_column(String(32), default="banking")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
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
