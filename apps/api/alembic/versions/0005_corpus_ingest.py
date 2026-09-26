"""how a corpus link was fetched, and the passages a judge read

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("corpus_items", sa.Column("ingest", sa.JSON(), nullable=True))
    op.add_column("eval_cycles", sa.Column("reference", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("eval_cycles", "reference")
    op.drop_column("corpus_items", "ingest")
