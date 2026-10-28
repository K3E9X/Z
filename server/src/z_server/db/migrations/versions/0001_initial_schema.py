"""Initial schema: every entity of docs/03 §1, pgvector, append-only audit log.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import os
import re

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# The memory column dimension must match the z-embed model (config memory.embedding_dim).
EMBEDDING_DIM = int(os.environ.get("Z_EMBEDDING_DIM", "1024"))
APP_ROLE = os.environ.get("Z_DB_APP_ROLE", "z_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", APP_ROLE):
    raise ValueError("Z_DB_APP_ROLE must be a plain role name")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "audit_checkpoints",
        sa.Column("seq", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_audit_checkpoints")),
    )
    op.create_table(
        "audit_events",
        sa.Column("seq", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("actor_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("session_id", sa.String(length=40), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("target_type", sa.String(length=60), nullable=False),
        sa.Column("target_id", sa.String(length=200), nullable=True),
        sa.Column("team", sa.String(length=120), nullable=True),
        sa.Column("result", sa.String(length=10), nullable=False),
        sa.Column("runs_as", sa.String(length=255), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_audit_events")),
        sa.UniqueConstraint("hash", name=op.f("uq_audit_events_hash")),
    )
    op.create_index("ix_audit_events_actor_ts", "audit_events", ["actor", "ts"], unique=False)
    op.create_index("ix_audit_events_target", "audit_events", ["target_type", "target_id"], unique=False)
    op.create_table(
        "budgets",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=200), nullable=False),
        sa.Column("limit", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.String(length=10), nullable=False),
        sa.Column("alert_at", sa.Double(), nullable=False),
        sa.Column("hard", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budgets")),
        sa.UniqueConstraint("scope", name=op.f("uq_budgets_scope")),
    )
    op.create_table(
        "channels",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("default_agent", sa.String(length=64), nullable=False),
        sa.Column("allowed_teams", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("identity_mapping", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channels")),
    )
    op.create_table(
        "connectors",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("data_class", sa.String(length=2), nullable=False),
        sa.Column("review_status", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pack", sa.String(length=64), nullable=True),
        sa.Column("health", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connectors")),
        sa.UniqueConstraint("name", name=op.f("uq_connectors_name")),
    )
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("target", sa.String(length=120), nullable=False),
        sa.Column("target_version", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("cases", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_runs")),
    )
    op.create_index(op.f("ix_eval_runs_target"), "eval_runs", ["target"], unique=False)
    op.create_table(
        "group_mappings",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("group_path", sa.String(length=300), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=True),
        sa.Column("scope", sa.String(length=10), nullable=True),
        sa.Column("team", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_mappings")),
    )
    op.create_table(
        "login_states",
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("redirect_to", sa.Text(), nullable=True),
        sa.Column("session_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state", name=op.f("pk_login_states")),
    )
    op.create_table(
        "memory_facts",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=10), nullable=False),
        sa.Column("scope_ref", sa.String(length=64), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=EMBEDDING_DIM), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data_class", sa.String(length=2), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_facts")),
    )
    op.create_table(
        "mission_suggestions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("objective_id", sa.String(length=40), nullable=True),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("playbook", sa.String(length=64), nullable=True),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("estimated_tokens", sa.BigInteger(), nullable=True),
        sa.Column("has_writes", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("mission_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mission_suggestions")),
    )
    op.create_table(
        "packs",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_packs")),
    )
    op.create_table(
        "proposals",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("target", sa.String(length=200), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("eval_before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("eval_after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("regressions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cost_delta", sa.Double(), nullable=True),
        sa.Column("shadow", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("decided_by", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposals")),
    )
    op.create_table(
        "providers",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data_classes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_providers")),
        sa.UniqueConstraint("name", name=op.f("uq_providers_name")),
    )
    op.create_table(
        "role_bindings",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_bindings")),
    )
    op.create_index("ix_role_bindings_subject", "role_bindings", ["subject"], unique=False)
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.UniqueConstraint("name", name=op.f("uq_roles_name")),
    )
    op.create_table(
        "routing_rules",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("match", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_routing_rules")),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_settings")),
    )
    op.create_table(
        "signals",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("mission_id", sa.String(length=40), nullable=True),
        sa.Column("job_id", sa.String(length=40), nullable=True),
        sa.Column("agent", sa.String(length=64), nullable=True),
        sa.Column("skill", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_signals")),
    )
    op.create_index(op.f("ix_signals_mission_id"), "signals", ["mission_id"], unique=False)
    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("pack", sa.String(length=64), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("evals", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skills")),
        sa.UniqueConstraint("name", name=op.f("uq_skills_name")),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_ref", sa.String(length=40), nullable=True),
        sa.Column("suggested_agent", sa.String(length=64), nullable=True),
        sa.Column("suggested_playbook", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("mission_id", sa.String(length=40), nullable=True),
        sa.Column("trigger_id", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("gitlab_groups", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("monthly_token_budget", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("name", name=op.f("uq_teams_name")),
    )
    op.create_table(
        "usage_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("bucket", sa.String(length=8), nullable=False),
        sa.Column("ts_bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=40), nullable=False),
        sa.Column("playbook", sa.String(length=64), nullable=False),
        sa.Column("team", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False),
        sa.Column("cached_tokens", sa.BigInteger(), nullable=False),
        sa.Column("requests", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_records")),
        sa.UniqueConstraint(
            "bucket", "ts_bucket", "model", "agent", "mission_id", "team", "user_id", name="uq_usage_records_key"
        ),
    )
    op.create_index("ix_usage_records_ts", "usage_records", ["bucket", "ts_bucket"], unique=False)
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("issuer", sa.String(length=255), nullable=False),
        sa.Column("sub", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reevaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_enforced", sa.Boolean(), nullable=False),
        sa.Column("local_kind", sa.String(length=20), nullable=True),
        sa.Column("groups", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gitlab_refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("issuer", "sub", name="uq_users_issuer_sub"),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("handle", sa.String(length=40), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("owner_team_id", sa.String(length=40), nullable=True),
        sa.Column("visibility", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("pack", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_team_id"], ["teams.id"], name=op.f("fk_agents_owner_team_id_teams"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint("handle", name=op.f("uq_agents_handle")),
        sa.UniqueConstraint("name", name=op.f("uq_agents_name")),
    )
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_api_tokens_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_api_tokens_token_hash")),
    )
    op.create_index(op.f("ix_api_tokens_user_id"), "api_tokens", ["user_id"], unique=False)
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=True),
        sa.Column("device", sa.String(length=255), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_auth_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
    )
    op.create_index("ix_auth_sessions_user", "auth_sessions", ["user_id"], unique=False)
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("surface", sa.String(length=12), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_chat_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_sessions")),
    )
    op.create_index(op.f("ix_chat_sessions_user_id"), "chat_sessions", ["user_id"], unique=False)
    op.create_table(
        "credentials",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("connector_id", sa.String(length=40), nullable=False),
        sa.Column("purpose", sa.String(length=10), nullable=False),
        sa.Column("scope", sa.String(length=10), nullable=False),
        sa.Column("owner_user_id", sa.String(length=40), nullable=True),
        sa.Column("backend", sa.String(length=20), nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("identity", sa.String(length=255), nullable=True),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_id"], ["connectors.id"], name=op.f("fk_credentials_connector_id_connectors"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name=op.f("fk_credentials_owner_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credentials")),
    )
    op.create_index(op.f("ix_credentials_connector_id"), "credentials", ["connector_id"], unique=False)
    op.create_table(
        "local_accounts",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("totp_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("role", sa.String(length=40), nullable=True),
        sa.Column("team", sa.String(length=120), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_local_accounts_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_local_accounts")),
        sa.UniqueConstraint("name", name=op.f("uq_local_accounts_name")),
    )
    op.create_table(
        "operations",
        sa.Column("id", sa.String(length=200), nullable=False),
        sa.Column("connector_id", sa.String(length=40), nullable=False),
        sa.Column("local_id", sa.String(length=200), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("class", sa.String(length=12), nullable=False),
        sa.Column("classifier_class", sa.String(length=12), nullable=False),
        sa.Column("exposed", sa.Boolean(), nullable=False),
        sa.Column("proposed_exposed", sa.Boolean(), nullable=False),
        sa.Column("rate_limit", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["connector_id"], ["connectors.id"], name=op.f("fk_operations_connector_id_connectors"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operations")),
    )
    op.create_index(op.f("ix_operations_connector_id"), "operations", ["connector_id"], unique=False)
    op.create_table(
        "playbooks",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pack", sa.String(length=64), nullable=True),
        sa.Column("owner_team_id", sa.String(length=40), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_team_id"], ["teams.id"], name=op.f("fk_playbooks_owner_team_id_teams"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playbooks")),
        sa.UniqueConstraint("name", name=op.f("uq_playbooks_name")),
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("skill_id", sa.String(length=40), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("proposal_id", sa.String(length=40), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skills.id"], name=op.f("fk_skill_versions_skill_id_skills"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_versions")),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_versions_skill_version"),
    )
    op.create_table(
        "user_teams",
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("team_id", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_user_teams_team_id_teams"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_teams_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "team_id", name=op.f("pk_user_teams")),
    )
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("agent_id", sa.String(length=40), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_versions_agent_id_agents"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_versions")),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )
    op.create_table(
        "assignments",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("agent_id", sa.String(length=40), nullable=False),
        sa.Column("object_kind", sa.String(length=20), nullable=False),
        sa.Column("object_ref", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_assignments_agent_id_agents"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignments")),
    )
    op.create_index(op.f("ix_assignments_agent_id"), "assignments", ["agent_id"], unique=False)
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("session_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("mission_id", sa.String(length=40), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name=op.f("fk_chat_messages_session_id_chat_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
    )
    op.create_index(op.f("ix_chat_messages_session_id"), "chat_messages", ["session_id"], unique=False)
    op.create_table(
        "missions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("playbook_id", sa.String(length=40), nullable=True),
        sa.Column("playbook_name", sa.String(length=64), nullable=True),
        sa.Column("playbook_version", sa.String(length=32), nullable=True),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("connectors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data_class", sa.String(length=2), nullable=False),
        sa.Column("autonomy", sa.String(length=12), nullable=False),
        sa.Column("plan_first", sa.Boolean(), nullable=False),
        sa.Column("four_eyes", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("waiting_reason", sa.String(length=20), nullable=True),
        sa.Column("created_by", sa.String(length=40), nullable=False),
        sa.Column("team_id", sa.String(length=40), nullable=True),
        sa.Column("session_id", sa.String(length=40), nullable=True),
        sa.Column("trigger_id", sa.String(length=40), nullable=True),
        sa.Column("budget", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False),
        sa.Column("llm_key_alias", sa.String(length=80), nullable=True),
        sa.Column("llm_key_enc", sa.LargeBinary(), nullable=True),
        sa.Column("next_seq", sa.BigInteger(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_missions_created_by_users")),
        sa.ForeignKeyConstraint(
            ["playbook_id"], ["playbooks.id"], name=op.f("fk_missions_playbook_id_playbooks"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_missions_team_id_teams"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_missions")),
    )
    op.create_index("ix_missions_status_created", "missions", ["status", "created_at"], unique=False)
    op.create_table(
        "objectives",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("agent_id", sa.String(length=40), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("owner_user_id", sa.String(length=40), nullable=False),
        sa.Column("last_value", sa.Double(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name=op.f("fk_objectives_agent_id_agents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name=op.f("fk_objectives_owner_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_objectives")),
    )
    op.create_index(op.f("ix_objectives_agent_id"), "objectives", ["agent_id"], unique=False)
    op.create_table(
        "personal_connections",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("connector_id", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("read_scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("write_scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("read_credential_id", sa.String(length=40), nullable=True),
        sa.Column("write_credential_id", sa.String(length=40), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name=op.f("fk_personal_connections_connector_id_connectors"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["read_credential_id"],
            ["credentials.id"],
            name=op.f("fk_personal_connections_read_credential_id_credentials"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_personal_connections_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["write_credential_id"],
            ["credentials.id"],
            name=op.f("fk_personal_connections_write_credential_id_credentials"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_personal_connections")),
    )
    op.create_index(op.f("ix_personal_connections_user_id"), "personal_connections", ["user_id"], unique=False)
    op.create_table(
        "playbook_versions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("playbook_id", sa.String(length=40), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["playbook_id"],
            ["playbooks.id"],
            name=op.f("fk_playbook_versions_playbook_id_playbooks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playbook_versions")),
        sa.UniqueConstraint("playbook_id", "version", name="uq_playbook_versions_playbook_version"),
    )
    op.create_table(
        "triggers",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("filter", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("playbook_id", sa.String(length=40), nullable=True),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("owner_user_id", sa.String(length=40), nullable=False),
        sa.Column("auto_start", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name=op.f("fk_triggers_owner_user_id_users")),
        sa.ForeignKeyConstraint(
            ["playbook_id"], ["playbooks.id"], name=op.f("fk_triggers_playbook_id_playbooks"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_triggers")),
    )
    op.create_table(
        "events",
        sa.Column("mission_id", sa.String(length=40), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=True),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["missions.id"], name=op.f("fk_events_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("mission_id", "seq", name=op.f("pk_events")),
    )
    op.create_index("ix_events_job_seq", "events", ["job_id", "seq"], unique=False)
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("mission_id", sa.String(length=40), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=40), nullable=False),
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("agent_version", sa.String(length=32), nullable=False),
        sa.Column("parent_job_id", sa.String(length=40), nullable=True),
        sa.Column("item_key", sa.String(length=200), nullable=True),
        sa.Column("item_index", sa.Integer(), nullable=True),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("needs_attention_reason", sa.String(length=30), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("resume_from_step", sa.Integer(), nullable=True),
        sa.Column("steps_done", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("tokens_in", sa.BigInteger(), nullable=False),
        sa.Column("tokens_out", sa.BigInteger(), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checkpoint", sa.LargeBinary(), nullable=True),
        sa.Column("checkpoint_step", sa.Integer(), nullable=True),
        sa.Column("bootstrap_token_hash", sa.String(length=64), nullable=True),
        sa.Column("session_token_hash", sa.String(length=64), nullable=True),
        sa.Column("control_state", sa.String(length=10), nullable=False),
        sa.Column("control_reason", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executor_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_jobs_agent_id_agents")),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["missions.id"], name=op.f("fk_jobs_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_job_id"], ["jobs.id"], name=op.f("fk_jobs_parent_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index("ix_jobs_mission_step", "jobs", ["mission_id", "step_id"], unique=False)
    op.create_index("ix_jobs_queue", "jobs", ["status", "available_at"], unique=False)
    op.create_table(
        "objective_checks",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("objective_id", sa.String(length=40), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("target", sa.Double(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("suggestions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_missions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["objective_id"],
            ["objectives.id"],
            name=op.f("fk_objective_checks_objective_id_objectives"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_objective_checks")),
    )
    op.create_index(op.f("ix_objective_checks_objective_id"), "objective_checks", ["objective_id"], unique=False)
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("mission_id", sa.String(length=40), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("data_class", sa.String(length=2), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_artifacts_job_id_jobs"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["missions.id"], name=op.f("fk_artifacts_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
    )
    op.create_index(op.f("ix_artifacts_job_id"), "artifacts", ["job_id"], unique=False)
    op.create_index(op.f("ix_artifacts_mission_id"), "artifacts", ["mission_id"], unique=False)
    op.create_table(
        "job_messages",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("by", sa.String(length=255), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_job_messages_job_id_jobs"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_messages")),
    )
    op.create_index(op.f("ix_job_messages_job_id"), "job_messages", ["job_id"], unique=False)
    op.create_table(
        "proposed_actions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("mission_id", sa.String(length=40), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=200), nullable=False),
        sa.Column("class", sa.String(length=12), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("preview", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reversible", sa.Boolean(), nullable=False),
        sa.Column("judge_verdict", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("runs_as", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("required_approvals", sa.Integer(), nullable=False),
        sa.Column("four_eyes", sa.Boolean(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("superseded_by", sa.String(length=40), nullable=True),
        sa.Column("edited_from", sa.String(length=40), nullable=True),
        sa.Column("apply_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_proposed_actions_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["missions.id"], name=op.f("fk_proposed_actions_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposed_actions")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_proposed_actions_idempotency_key")),
    )
    op.create_index(op.f("ix_proposed_actions_mission_id"), "proposed_actions", ["mission_id"], unique=False)
    op.create_index("ix_proposed_actions_status", "proposed_actions", ["status"], unique=False)
    op.create_table(
        "questions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("mission_id", sa.String(length=40), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answered_by", sa.String(length=255), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], name=op.f("fk_questions_job_id_jobs"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["mission_id"], ["missions.id"], name=op.f("fk_questions_mission_id_missions"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_questions")),
    )
    op.create_index(op.f("ix_questions_mission_id"), "questions", ["mission_id"], unique=False)
    op.create_table(
        "action_approvals",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("action_id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step_up", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["action_id"],
            ["proposed_actions.id"],
            name=op.f("fk_action_approvals_action_id_proposed_actions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_action_approvals_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_approvals")),
        sa.UniqueConstraint("action_id", "user_id", name="uq_action_approvals_action_user"),
    )
    op.execute("CREATE INDEX ix_memory_facts_embedding ON memory_facts USING hnsw (embedding vector_cosine_ops)")
    # Audit log: append-only. A trigger refuses UPDATE and DELETE unless the maintenance role runs retention
    # with z.audit_maintenance=on; the application role only gets SELECT and INSERT (docs/12 §4).
    op.execute(
        """
        CREATE FUNCTION z_audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' AND current_setting('z.audit_maintenance', true) = 'on' THEN
            RETURN OLD;
          END IF;
          RAISE EXCEPTION 'audit_events is append-only (%)', TG_OP USING ERRCODE = 'insufficient_privilege';
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION z_audit_append_only()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION z_audit_append_only()"
    )
    grants = f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            GRANT USAGE ON SCHEMA public TO {APP_ROLE};
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};
            REVOKE UPDATE, DELETE, TRUNCATE ON audit_events, audit_checkpoints FROM {APP_ROLE};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
          END IF;
        END $$;
        """  # noqa: S608 (APP_ROLE is validated above)
    op.execute(grants)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP INDEX IF EXISTS ix_memory_facts_embedding")
    op.drop_table("action_approvals")
    op.drop_index(op.f("ix_questions_mission_id"), table_name="questions")
    op.drop_table("questions")
    op.drop_index("ix_proposed_actions_status", table_name="proposed_actions")
    op.drop_index(op.f("ix_proposed_actions_mission_id"), table_name="proposed_actions")
    op.drop_table("proposed_actions")
    op.drop_index(op.f("ix_job_messages_job_id"), table_name="job_messages")
    op.drop_table("job_messages")
    op.drop_index(op.f("ix_artifacts_mission_id"), table_name="artifacts")
    op.drop_index(op.f("ix_artifacts_job_id"), table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index(op.f("ix_objective_checks_objective_id"), table_name="objective_checks")
    op.drop_table("objective_checks")
    op.drop_index("ix_jobs_queue", table_name="jobs")
    op.drop_index("ix_jobs_mission_step", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_events_job_seq", table_name="events")
    op.drop_table("events")
    op.drop_table("triggers")
    op.drop_table("playbook_versions")
    op.drop_index(op.f("ix_personal_connections_user_id"), table_name="personal_connections")
    op.drop_table("personal_connections")
    op.drop_index(op.f("ix_objectives_agent_id"), table_name="objectives")
    op.drop_table("objectives")
    op.drop_index("ix_missions_status_created", table_name="missions")
    op.drop_table("missions")
    op.drop_index(op.f("ix_chat_messages_session_id"), table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index(op.f("ix_assignments_agent_id"), table_name="assignments")
    op.drop_table("assignments")
    op.drop_table("agent_versions")
    op.drop_table("user_teams")
    op.drop_table("skill_versions")
    op.drop_table("playbooks")
    op.drop_index(op.f("ix_operations_connector_id"), table_name="operations")
    op.drop_table("operations")
    op.drop_table("local_accounts")
    op.drop_index(op.f("ix_credentials_connector_id"), table_name="credentials")
    op.drop_table("credentials")
    op.drop_index(op.f("ix_chat_sessions_user_id"), table_name="chat_sessions")
    op.drop_table("chat_sessions")
    op.drop_index("ix_auth_sessions_user", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index(op.f("ix_api_tokens_user_id"), table_name="api_tokens")
    op.drop_table("api_tokens")
    op.drop_table("agents")
    op.drop_table("users")
    op.drop_index("ix_usage_records_ts", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_table("teams")
    op.drop_table("tasks")
    op.drop_table("skills")
    op.drop_index(op.f("ix_signals_mission_id"), table_name="signals")
    op.drop_table("signals")
    op.drop_table("settings")
    op.drop_table("routing_rules")
    op.drop_table("roles")
    op.drop_index("ix_role_bindings_subject", table_name="role_bindings")
    op.drop_table("role_bindings")
    op.drop_table("providers")
    op.drop_table("proposals")
    op.drop_table("packs")
    op.drop_table("mission_suggestions")
    op.drop_table("memory_facts")
    op.drop_table("login_states")
    op.drop_table("group_mappings")
    op.drop_index(op.f("ix_eval_runs_target"), table_name="eval_runs")
    op.drop_table("eval_runs")
    op.drop_table("connectors")
    op.drop_table("channels")
    op.drop_table("budgets")
    op.drop_index("ix_audit_events_target", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_ts", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("audit_checkpoints")
    op.execute("DROP FUNCTION IF EXISTS z_audit_append_only()")
