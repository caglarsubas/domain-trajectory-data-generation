"""job results and projects, and live key checks

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=True))
    op.add_column("jobs", sa.Column("result", sa.JSON(), nullable=True))
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.add_column("credentials", sa.Column("check_status", sa.String(16), nullable=True))
    op.add_column("credentials", sa.Column("check_detail", sa.Text(), nullable=True))
    op.add_column("credentials", sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("credentials", "checked_at")
    op.drop_column("credentials", "check_detail")
    op.drop_column("credentials", "check_status")
    op.drop_index("ix_jobs_project_id", table_name="jobs")
    op.drop_column("jobs", "result")
    op.drop_column("jobs", "project_id")
