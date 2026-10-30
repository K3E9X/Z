"""Typed connector secrets (ADR 0033): a service credential may hold a value sealed with the platform key.

Expand-only: one nullable column. A row with a sealed value has backend `sealed` and a non-secret ref
`sealed:<connector>/<purpose>`; every other row keeps its secrets-backend reference and a NULL value.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("credentials", sa.Column("sealed_value", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("credentials", "sealed_value")
