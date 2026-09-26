"""judged samples, models, agreement, and audit flags

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CYCLE_COLUMNS = ("sample", "models", "scores", "agreement", "flags", "canary")


def upgrade() -> None:
    for name in CYCLE_COLUMNS:
        op.add_column("eval_cycles", sa.Column(name, sa.JSON(), nullable=True))
    op.add_column("eval_verdicts", sa.Column("trajectory_id", sa.String(64), nullable=True))
    op.add_column("eval_verdicts", sa.Column("pair_order", sa.String(4), nullable=True))
    op.add_column("eval_verdicts", sa.Column("canary", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("eval_verdicts", "canary")
    op.drop_column("eval_verdicts", "pair_order")
    op.drop_column("eval_verdicts", "trajectory_id")
    for name in reversed(CYCLE_COLUMNS):
        op.drop_column("eval_cycles", name)
