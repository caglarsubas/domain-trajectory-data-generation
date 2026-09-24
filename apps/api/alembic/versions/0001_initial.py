"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_accounts_email", "accounts", ["email"], unique=True)
    op.create_table(
        "credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("ready", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("sector", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "corpus_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("provenance", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("parent_run_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("inherited_feedback_ids", sa.JSON(), nullable=False),
        sa.Column("candidate", sa.JSON(), nullable=True),
        sa.Column("cycle_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("stance", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "eval_cycles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("cycle_index", sa.Integer(), nullable=False),
        sa.Column("hard_check_passed", sa.Integer(), nullable=False),
        sa.Column("hard_check_errors", sa.JSON(), nullable=False),
        sa.Column("reference_quality", sa.String(16), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("revision_notes", sa.JSON(), nullable=False),
        sa.Column("judge_tenant", sa.String(120), nullable=False),
        sa.Column("judge_org_id", sa.String(120), nullable=False),
        sa.Column("judge_key_id", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "eval_verdicts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cycle_id", sa.String(36), sa.ForeignKey("eval_cycles.id"), nullable=False),
        sa.Column("rubric", sa.String(64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("parsed", sa.JSON(), nullable=False),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column("judge_model", sa.String(200), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("eval_verdicts")
    op.drop_table("eval_cycles")
    op.drop_table("feedback")
    op.drop_table("runs")
    op.drop_table("corpus_items")
    op.drop_table("projects")
    op.drop_table("credentials")
    op.drop_table("accounts")
