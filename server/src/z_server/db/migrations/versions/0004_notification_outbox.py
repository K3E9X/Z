"""Notification outbox (ADR 0023).

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("Z_DB_APP_ROLE", "z_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise ValueError("Z_DB_APP_ROLE must be a plain role name")


def upgrade() -> None:
    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("mission_id", sa.String(length=40), sa.ForeignKey("missions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("event_seq", sa.BigInteger(), nullable=True),
        sa.Column("team_id", sa.String(length=40), nullable=True),
        sa.Column("data_class", sa.String(length=2), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("delivered", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notification_outbox_pending", "notification_outbox", ["done", "next_at"])
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON notification_outbox TO {APP_ROLE};
            GRANT USAGE, SELECT ON SEQUENCE notification_outbox_id_seq TO {APP_ROLE};
          END IF;
        END $$;
        """  # noqa: S608 (APP_ROLE is validated above)
    )


def downgrade() -> None:
    op.drop_index("ix_notification_outbox_pending", table_name="notification_outbox")
    op.drop_table("notification_outbox")
