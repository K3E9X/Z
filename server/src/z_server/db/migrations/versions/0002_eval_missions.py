"""Eval missions and candidate versions (docs/08 §4, §6).

Missions started by an eval run capture their writes and never apply them; an eval run may carry candidate
skill or playbook text (a learning proposal under evaluation) that its missions use instead of the current one.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.dialects.postgresql
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("missions", sa.Column("eval_run_id", sa.String(length=40), nullable=True))
    op.create_index("ix_missions_eval_run_id", "missions", ["eval_run_id"])
    op.add_column("eval_runs", sa.Column("overrides", sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column("eval_runs", sa.Column("proposal_id", sa.String(length=40), nullable=True))
    op.add_column("proposals", sa.Column("candidate", sa.dialects.postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("proposals", "candidate")
    op.drop_column("eval_runs", "proposal_id")
    op.drop_column("eval_runs", "overrides")
    op.drop_index("ix_missions_eval_run_id", table_name="missions")
    op.drop_column("missions", "eval_run_id")
