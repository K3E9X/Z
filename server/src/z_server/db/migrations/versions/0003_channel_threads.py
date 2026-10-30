"""Chat threads that follow a mission started from a channel (docs/05 §10, ADR 0017).

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("Z_DB_APP_ROLE", "z_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise ValueError("Z_DB_APP_ROLE must be a plain role name")


def upgrade() -> None:
    op.create_table(
        "channel_threads",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("channel_id", sa.String(length=40), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mission_id", sa.String(length=40), sa.ForeignKey("missions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_enc", sa.LargeBinary(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="-1"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mission_id", name="uq_channel_threads_mission_id"),
    )
    op.create_index("ix_channel_threads_status", "channel_threads", ["status"])
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON channel_threads TO {APP_ROLE};
          END IF;
        END $$;
        """  # noqa: S608 (APP_ROLE is validated above)
    )


def downgrade() -> None:
    op.drop_index("ix_channel_threads_status", table_name="channel_threads")
    op.drop_table("channel_threads")
