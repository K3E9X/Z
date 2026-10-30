"""Tables for every entity of docs/03 §1. Column names follow the contracts.

Conventions: string primary keys with a prefix (z_server.ids), `jsonb` for manifests and payloads,
timezone-aware timestamps, enums stored as text (validated by the contracts at the API boundary).
`audit_events` is append-only for the application role (see the initial migration).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from z_server.db.base import Base, Timestamps, utcnow

# ---------------------------------------------------------------- identity and access


class Team(Base, Timestamps):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    gitlab_groups: Mapped[list[Any]] = mapped_column(default=list)
    monthly_token_budget: Mapped[int | None] = mapped_column(BigInteger)


class User(Base, Timestamps):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("issuer", "sub", name="uq_users_issuer_sub"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(255))
    sub: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_login_at: Mapped[datetime | None]
    last_reevaluated_at: Mapped[datetime | None]
    mfa_enforced: Mapped[bool] = mapped_column(Boolean, default=False)
    local_kind: Mapped[str | None] = mapped_column(String(20))
    groups: Mapped[list[Any]] = mapped_column(default=list)  # GitLab group paths at last evaluation
    gitlab_refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)


class UserTeam(Base):
    __tablename__ = "user_teams"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), default="gitlab")


class Role(Base, Timestamps):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text)
    permissions: Mapped[list[Any]] = mapped_column(default=list)


class RoleBinding(Base):
    __tablename__ = "role_bindings"
    __table_args__ = (Index("ix_role_bindings_subject", "subject"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(40))
    scope: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime | None]
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class GroupMapping(Base):
    __tablename__ = "group_mappings"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    group_path: Mapped[str] = mapped_column(String(300))
    role: Mapped[str | None] = mapped_column(String(40))
    scope: Mapped[str | None] = mapped_column(String(10))
    team: Mapped[str | None] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(10), default="db")


class Setting(Base):
    """Admin-edited settings that override config/z.yaml (group mapping, guardrails, audit, routing)."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(255))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user", "user_id"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str | None] = mapped_column(String(64))
    device: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    step_up_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(String(255))


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    scopes: Mapped[list[Any]] = mapped_column(default=list)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LocalAccount(Base):
    __tablename__ = "local_accounts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # bootstrap | test | breakglass
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    password_hash: Mapped[str] = mapped_column(Text)
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    role: Mapped[str | None] = mapped_column(String(40))
    team: Mapped[str | None] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LoginState(Base):
    """OIDC authorization requests in flight (state, nonce, PKCE verifier)."""

    __tablename__ = "login_states"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64))
    code_verifier: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(20))  # login | step_up | connection
    redirect_to: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]


# ---------------------------------------------------------------- catalog: packs, agents, skills, playbooks


class PackRecord(Base, Timestamps):
    __tablename__ = "packs"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))


class Agent(Base, Timestamps):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(64))
    handle: Mapped[str] = mapped_column(String(40), unique=True)
    version: Mapped[str] = mapped_column(String(32))
    profile: Mapped[dict[str, Any]]
    owner_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    visibility: Mapped[str] = mapped_column(String(10), default="team")
    status: Mapped[str] = mapped_column(String(20), default="available")
    pack: Mapped[str | None] = mapped_column(String(64))


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(32))
    profile: Mapped[dict[str, Any]]
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Skill(Base, Timestamps):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(10))
    pack: Mapped[str | None] = mapped_column(String(64))
    path: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    evals: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(120))


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_versions_skill_version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(10))
    proposal_id: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Playbook(Base, Timestamps):
    __tablename__ = "playbooks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    version: Mapped[str] = mapped_column(String(32))
    manifest: Mapped[dict[str, Any]]
    pack: Mapped[str | None] = mapped_column(String(64))
    owner_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None]


class PlaybookVersion(Base):
    __tablename__ = "playbook_versions"
    __table_args__ = (UniqueConstraint("playbook_id", "version", name="uq_playbook_versions_playbook_version"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    playbook_id: Mapped[str] = mapped_column(ForeignKey("playbooks.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(32))
    manifest: Mapped[dict[str, Any]]
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------- connectors


class Connector(Base, Timestamps):
    __tablename__ = "connectors"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    display_name: Mapped[str] = mapped_column(String(120))
    base_url: Mapped[str | None] = mapped_column(Text)
    data_class: Mapped[str] = mapped_column(String(2), default="C2")
    review_status: Mapped[str] = mapped_column(String(20), default="pending_review")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    manifest: Mapped[dict[str, Any]]
    pack: Mapped[str | None] = mapped_column(String(64))
    health: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)  # full operation id
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"), index=True)
    local_id: Mapped[str] = mapped_column(String(200))
    source_ref: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    op_class: Mapped[str] = mapped_column("class", String(12))
    classifier_class: Mapped[str] = mapped_column(String(12))
    exposed: Mapped[bool] = mapped_column(Boolean, default=False)
    proposed_exposed: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limit: Mapped[dict[str, Any] | None]
    input_schema: Mapped[dict[str, Any]] = mapped_column(default=dict)
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None]


class Credential(Base):
    __tablename__ = "credentials"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(10))
    scope: Mapped[str] = mapped_column(String(10))
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    backend: Mapped[str] = mapped_column(String(20))
    ref: Mapped[str] = mapped_column(Text)
    # ADR 0033: a secret typed in the web app, sealed with the platform key (backend "sealed"); never returned
    sealed_value: Mapped[bytes | None] = mapped_column(LargeBinary)
    identity: Mapped[str | None] = mapped_column(String(255))
    scopes: Mapped[list[Any]] = mapped_column(default=list)
    rotated_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PersonalConnection(Base):
    __tablename__ = "personal_connections"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("connectors.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(12), default="active")
    read_scopes: Mapped[list[Any]] = mapped_column(default=list)
    write_scopes: Mapped[list[Any]] = mapped_column(default=list)
    read_credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id", ondelete="SET NULL"))
    write_credential_id: Mapped[str | None] = mapped_column(ForeignKey("credentials.id", ondelete="SET NULL"))
    last_used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Channel(Base, Timestamps):
    __tablename__ = "channels"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    default_agent: Mapped[str] = mapped_column(String(64))
    allowed_teams: Mapped[list[Any]] = mapped_column(default=list)
    identity_mapping: Mapped[str] = mapped_column(String(20), default="sso_email")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class ChannelThread(Base, Timestamps):
    """Where a mission started from a chat is followed up (docs/05 §10). `target_enc` is the sealed reply target
    (Teams conversation, Slack thread or a response URL); `last_seq` is the last mission event handled."""

    __tablename__ = "channel_threads"
    __table_args__ = (Index("ix_channel_threads_status", "status"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), unique=True)
    target_enc: Mapped[bytes] = mapped_column(LargeBinary)
    last_seq: Mapped[int] = mapped_column(BigInteger, default=-1)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | closed | failed
    failures: Mapped[int] = mapped_column(Integer, default=0)


class NotificationOutbox(Base):
    """Transactional outbox of notifications (ADR 0023): written with the change, delivered by a worker.
    `delivered` lists the target ids that accepted it; a row is done when every matching target did, or after
    the last attempt."""

    __tablename__ = "notification_outbox"
    __table_args__ = (Index("ix_notification_outbox_pending", "done", "next_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40))
    mission_id: Mapped[str | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    event_seq: Mapped[int | None] = mapped_column(BigInteger)
    team_id: Mapped[str | None] = mapped_column(String(40))
    data_class: Mapped[str] = mapped_column(String(2))
    payload: Mapped[dict[str, Any]]
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime]
    done: Mapped[bool] = mapped_column(default=False)
    delivered: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------- missions and jobs


class Mission(Base, Timestamps):
    __tablename__ = "missions"
    __table_args__ = (Index("ix_missions_status_created", "status", "created_at"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str | None] = mapped_column(Text)
    playbook_id: Mapped[str | None] = mapped_column(ForeignKey("playbooks.id", ondelete="SET NULL"))
    playbook_name: Mapped[str | None] = mapped_column(String(64))
    playbook_version: Mapped[str | None] = mapped_column(String(32))
    plan: Mapped[dict[str, Any] | None]
    inputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    connectors: Mapped[list[Any]] = mapped_column(default=list)
    data_class: Mapped[str] = mapped_column(String(2), default="C2")
    autonomy: Mapped[str] = mapped_column(String(12), default="propose")
    plan_first: Mapped[bool] = mapped_column(Boolean, default=False)
    four_eyes: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    waiting_reason: Mapped[str | None] = mapped_column(String(20))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    session_id: Mapped[str | None] = mapped_column(String(40))
    trigger_id: Mapped[str | None] = mapped_column(String(40))
    eval_run_id: Mapped[str | None] = mapped_column(String(40), index=True)  # writes captured, never applied
    budget: Mapped[dict[str, Any]] = mapped_column(default=dict)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0)
    llm_key_alias: Mapped[str | None] = mapped_column(String(80))
    llm_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    next_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]
    deadline_at: Mapped[datetime | None]


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_queue", "status", "available_at"),
        Index("ix_jobs_mission_step", "mission_id", "step_id"),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    step_id: Mapped[str] = mapped_column(String(64))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    agent_name: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[str] = mapped_column(String(32))
    parent_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    item_key: Mapped[str | None] = mapped_column(String(200))
    item_index: Mapped[int | None] = mapped_column(Integer)
    input: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prompt: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    needs_attention_reason: Mapped[str | None] = mapped_column(String(30))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    resume_from_step: Mapped[int | None] = mapped_column(Integer)
    steps_done: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0)
    token_cap: Mapped[int | None] = mapped_column(BigInteger)  # a sub-agent's share of its parent's budget
    output: Mapped[dict[str, Any] | None]
    error: Mapped[dict[str, Any] | None]
    checkpoint: Mapped[bytes | None] = mapped_column(LargeBinary)
    checkpoint_step: Mapped[int | None] = mapped_column(Integer)
    bootstrap_token_hash: Mapped[str | None] = mapped_column(String(64))
    session_token_hash: Mapped[str | None] = mapped_column(String(64))
    control_state: Mapped[str] = mapped_column(String(10), default="run")
    control_reason: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None]
    available_at: Mapped[datetime] = mapped_column(default=utcnow)
    deadline_at: Mapped[datetime | None]
    executor_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]


class JobMessage(Base):
    """Guide-the-agent messages waiting to be delivered to a job."""

    __tablename__ = "job_messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    by: Mapped[str] = mapped_column(String(255))
    delivered_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_job_seq", "job_id", "seq"),)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(String(40))
    ts: Mapped[datetime]
    type: Mapped[str] = mapped_column(String(30))
    agent: Mapped[str | None] = mapped_column(String(64))
    step_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]]


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    data_class: Mapped[str] = mapped_column(String(2))
    storage_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ProposedAction(Base, Timestamps):
    __tablename__ = "proposed_actions"
    __table_args__ = (Index("ix_proposed_actions_status", "status"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    agent: Mapped[str] = mapped_column(String(64))
    connector: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(200))
    op_class: Mapped[str] = mapped_column("class", String(12))
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]]
    preview: Mapped[dict[str, Any]]
    reversible: Mapped[bool] = mapped_column(Boolean, default=True)
    judge_verdict: Mapped[dict[str, Any]] = mapped_column(default=dict)
    runs_as: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(12), default="pending")
    required_approvals: Mapped[int] = mapped_column(Integer, default=1)
    four_eyes: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict[str, Any] | None]
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    superseded_by: Mapped[str | None] = mapped_column(String(40))
    edited_from: Mapped[str | None] = mapped_column(String(40))
    apply_attempts: Mapped[int] = mapped_column(Integer, default=0)


class ActionApproval(Base):
    __tablename__ = "action_approvals"
    __table_args__ = (UniqueConstraint("action_id", "user_id", name="uq_action_approvals_action_user"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("proposed_actions.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(default=utcnow)
    step_up: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str | None] = mapped_column(Text)


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    agent: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    options: Mapped[list[Any]] = mapped_column(default=list)
    answer: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[str | None] = mapped_column(String(255))
    answered_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class MissionSuggestion(Base):
    __tablename__ = "mission_suggestions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    objective_id: Mapped[str | None] = mapped_column(String(40))
    agent: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    playbook: Mapped[str | None] = mapped_column(String(64))
    inputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    estimated_tokens: Mapped[int | None] = mapped_column(BigInteger)
    has_writes: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="open")
    mission_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------- intake, sessions, objectives


class Task(Base, Timestamps):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source: Mapped[str] = mapped_column(String(12))
    external_ref: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text)
    body_ref: Mapped[str | None] = mapped_column(String(40))
    suggested_agent: Mapped[str | None] = mapped_column(String(64))
    suggested_playbook: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="incoming")
    mission_id: Mapped[str | None] = mapped_column(String(40))
    trigger_id: Mapped[str | None] = mapped_column(String(40))


class Trigger(Base, Timestamps):
    __tablename__ = "triggers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))
    name: Mapped[str | None] = mapped_column(String(120))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    filter: Mapped[dict[str, Any]] = mapped_column(default=dict)
    playbook_id: Mapped[str | None] = mapped_column(ForeignKey("playbooks.id", ondelete="CASCADE"))
    inputs: Mapped[dict[str, Any]] = mapped_column(default=dict)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    auto_start: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    last_fired_at: Mapped[datetime | None]
    next_fire_at: Mapped[datetime | None]


class ChatSession(Base, Timestamps):
    """A CLI or chat conversation (contract entity `Session`)."""

    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    surface: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(Text, default="")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(Text)
    mission_id: Mapped[str | None] = mapped_column(String(40))
    ts: Mapped[datetime] = mapped_column(default=utcnow)


class Objective(Base, Timestamps):
    __tablename__ = "objectives"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    manifest: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(String(12), default="on_track")
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    last_value: Mapped[float | None]
    last_checked_at: Mapped[datetime | None]
    next_check_at: Mapped[datetime | None]


class ObjectiveCheck(Base):
    __tablename__ = "objective_checks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    objective_id: Mapped[str] = mapped_column(ForeignKey("objectives.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    value: Mapped[float]
    target: Mapped[float]
    status: Mapped[str] = mapped_column(String(12))
    explanation: Mapped[str | None] = mapped_column(Text)
    suggestions: Mapped[list[Any]] = mapped_column(default=list)
    started_missions: Mapped[list[Any]] = mapped_column(default=list)


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    object_kind: Mapped[str] = mapped_column(String(20))
    object_ref: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(10), default="owner")
    since: Mapped[datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------- learning, memory, evals


class MemoryFact(Base):
    __tablename__ = "memory_facts"
    __table_args__ = (
        Index(
            "ix_memory_facts_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    scope: Mapped[str] = mapped_column(String(10))
    scope_ref: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector())
    source: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    data_class: Mapped[str] = mapped_column(String(2))
    expires_at: Mapped[datetime | None]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    mission_id: Mapped[str | None] = mapped_column(String(40), index=True)
    job_id: Mapped[str | None] = mapped_column(String(40))
    agent: Mapped[str | None] = mapped_column(String(64))
    skill: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ts: Mapped[datetime] = mapped_column(default=utcnow)


class Proposal(Base, Timestamps):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    target: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(Text)
    diff: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list[Any]] = mapped_column(default=list)
    eval_before: Mapped[dict[str, Any] | None]
    eval_after: Mapped[dict[str, Any] | None]
    regressions: Mapped[list[Any]] = mapped_column(default=list)
    cost_delta: Mapped[float | None]
    shadow: Mapped[dict[str, Any] | None]
    status: Mapped[str] = mapped_column(String(12), default="observed")
    candidate: Mapped[dict[str, Any] | None]  # what applying writes: skill text, fact, rule or playbook manifest
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None]


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    target: Mapped[str] = mapped_column(String(120), index=True)
    target_version: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="queued")
    scores: Mapped[dict[str, Any] | None]
    passed: Mapped[bool | None]
    cases: Mapped[list[Any]] = mapped_column(default=list)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    ended_at: Mapped[datetime | None]
    overrides: Mapped[dict[str, Any] | None]  # {"skills": {name: content}} for a proposal under evaluation
    proposal_id: Mapped[str | None] = mapped_column(String(40))


# ---------------------------------------------------------------- inference, usage, budgets


class Provider(Base, Timestamps):
    __tablename__ = "providers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(10))
    aliases: Mapped[list[Any]] = mapped_column(default=list)
    data_classes: Mapped[list[Any]] = mapped_column(default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class RoutingRule(Base):
    __tablename__ = "routing_rules"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    match: Mapped[dict[str, Any]]
    target: Mapped[dict[str, Any]]
    origin: Mapped[str] = mapped_column(String(10), default="config")
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Budget(Base):
    __tablename__ = "budgets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    scope: Mapped[str] = mapped_column(String(200), unique=True)
    limit: Mapped[int] = mapped_column(BigInteger)
    period: Mapped[str] = mapped_column(String(10))
    alert_at: Mapped[float] = mapped_column(default=0.8)
    hard: Mapped[bool] = mapped_column(Boolean, default=True)


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint(
            "bucket", "ts_bucket", "model", "agent", "mission_id", "team", "user_id", name="uq_usage_records_key"
        ),
        Index("ix_usage_records_ts", "bucket", "ts_bucket"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bucket: Mapped[str] = mapped_column(String(8))  # minute | hour | day
    ts_bucket: Mapped[datetime]
    model: Mapped[str] = mapped_column(String(64), default="")
    agent: Mapped[str] = mapped_column(String(64), default="")
    mission_id: Mapped[str] = mapped_column(String(40), default="")
    playbook: Mapped[str] = mapped_column(String(64), default="")
    team: Mapped[str] = mapped_column(String(120), default="")
    user_id: Mapped[str] = mapped_column(String(40), default="")
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0)
    cached_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    requests: Mapped[int] = mapped_column(BigInteger, default=0)


# ---------------------------------------------------------------- audit (append-only)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_actor_ts", "actor", "ts"),
        Index("ix_audit_events_target", "target_type", "target_id"),
    )
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    ts: Mapped[datetime]
    actor: Mapped[str] = mapped_column(String(255))
    actor_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(120))
    target_type: Mapped[str] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(200))
    team: Mapped[str | None] = mapped_column(String(120))
    result: Mapped[str] = mapped_column(String(10))
    runs_as: Mapped[str | None] = mapped_column(String(255))
    before: Mapped[Any] = mapped_column(JSONB, nullable=True)
    after: Mapped[Any] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class AuditCheckpoint(Base):
    __tablename__ = "audit_checkpoints"
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    hash: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str] = mapped_column(Text)
    key_id: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(default=utcnow)
