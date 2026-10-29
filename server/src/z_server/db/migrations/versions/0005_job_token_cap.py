"""Job token cap: a sub-agent gets a share of its parent's remaining budget (docs/04 §2).

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("token_cap", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "token_cap")
