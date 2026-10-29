"""API resources: identity, access control, sessions, tokens, admin and audit (openapi components)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from z_common.models.base import Handle, Id, Permission, RoleName, Scope, Subject, ZModel
from z_common.models.enums import BindingSource, DataClass, MappingSource, SessionKind, UserStatus


class EffectiveRole(ZModel):
    role: RoleName
    scope: Scope
    source: BindingSource
    via: str | None = None
    expires_at: datetime | None = None


class TeamRef(ZModel):
    id: Id
    name: str


class User(ZModel):
    id: Id
    issuer: str | None = None
    sub: str | None = None
    username: str
    name: str
    email: str | None = None
    avatar_url: str | None = None
    teams: list[TeamRef]
    status: UserStatus
    last_login_at: datetime | None = None
    mfa_enforced: bool | None = None
    local_kind: Literal["bootstrap", "test", "breakglass", "dev"] | None = None
    effective_roles: list[EffectiveRole]
    created_at: datetime | None = None


class PermissionGrant(ZModel):
    permission: Permission
    scope: Scope


class SessionInfo(ZModel):
    id: Id
    kind: SessionKind
    expires_at: datetime
    step_up_at: datetime | None = None


class Banner(ZModel):
    kind: Literal["local_account", "breakglass", "maintenance", "budget"]
    text: str


class WorkspaceInfo(ZModel):
    name: str
    environment: str | None = None


class Me(ZModel):
    user: User
    permissions: list[PermissionGrant]
    session: SessionInfo
    banners: list[Banner]
    csrf_token: str | None = None
    workspace: WorkspaceInfo


class Team(ZModel):
    id: Id
    name: str
    gitlab_groups: list[str] | None = None
    members_count: int | None = None
    monthly_token_budget: int | None = None
    created_at: datetime | None = None


class TeamWrite(ZModel):
    name: str
    monthly_token_budget: int | None = None


class Role(ZModel):
    id: Id
    name: RoleName
    builtin: bool
    description: str | None = None
    permissions: list[Permission]


class RoleWrite(ZModel):
    name: RoleName
    description: str | None = None
    permissions: list[Permission]


class PermissionInfo(ZModel):
    permission: Permission
    description: str
    roles: list[RoleName]


class RoleBinding(ZModel):
    id: Id
    subject: Subject
    role: RoleName
    scope: Scope
    source: BindingSource
    expires_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime


class RoleBindingWrite(ZModel):
    subject: Subject
    role: RoleName
    scope: Scope
    expires_at: datetime | None = None
    reason: str | None = None


class GroupMapping(ZModel):
    id: Id
    group_path: str
    role: RoleName | None = None
    scope: Literal["org", "team"] | None = None
    team: str | None = None
    source: MappingSource


class GroupRoleMapping(ZModel):
    group: str
    role: RoleName
    scope: Literal["org", "team"]


class GroupTeamMapping(ZModel):
    group: str
    team: str


class GroupMappingConfig(ZModel):
    allowed_groups: list[str]
    no_match: Literal["viewer", "deny"]
    group_mapping: list[GroupRoleMapping]
    team_mapping: list[GroupTeamMapping]


class AccessChange(ZModel):
    user: str
    role: str | None = None
    scope: str | None = None
    team: str | None = None
    change: Literal["gain", "loss"]


class GroupMappingDryRun(ZModel):
    changes: list[AccessChange]
    users_evaluated: int
    denied_users: list[str] | None = None


class WhoCanEntry(ZModel):
    user: str
    scope: Scope
    via: str


class WhoCan(ZModel):
    permission: Permission
    users: list[WhoCanEntry]


class AuthSession(ZModel):
    id: Id
    user: str
    kind: SessionKind
    device: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    step_up_at: datetime | None = None
    revoked_at: datetime | None = None
    current: bool | None = None


class ApiToken(ZModel):
    id: Id
    user: str
    name: str
    scopes: list[Permission]
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    token: str | None = None


class ApiTokenCreate(ZModel):
    name: str
    scopes: list[Permission]
    expires_in_days: Annotated[int, Field(ge=1, le=90)]


class SessionLifetimes(ZModel):
    idle_minutes: int | None = None
    absolute_hours: int | None = None
    cli_token_days: int | None = None


class JwksStatus(ZModel):
    ok: bool
    keys: int | None = None
    fetched_at: datetime | None = None
    error: str | None = None


class BootstrapState(ZModel):
    enabled: bool
    state: Literal["active", "disabled", "expired"]
    expires_at: datetime | None = None


class TestAccountInfo(ZModel):
    name: str
    role: str
    team: str | None = None
    expires_at: datetime | None = None


class BreakglassState(ZModel):
    enabled: bool


class LocalAccountsState(ZModel):
    bootstrap: BootstrapState | None = None
    test_accounts: list[TestAccountInfo] | None = None
    breakglass: BreakglassState | None = None


class AuthSettings(ZModel):
    mode: Literal["oidc", "dev"]
    issuer_url: str
    client_id: str
    client_secret: str | None = None  # write-only
    cli_client_id: str | None = None
    scopes: list[str]
    groups_claim: str
    session: SessionLifetimes | None = None
    step_up_max_age_s: int | None = None
    jwks: JwksStatus | None = None
    local_accounts: LocalAccountsState | None = None


class AuthSettingsTest(ZModel):
    ok: bool
    discovery: bool | None = None
    jwks: bool | None = None
    detail: str | None = None


class CliAuthConfig(ZModel):
    mode: Literal["oidc", "dev"]
    issuer_url: str
    client_id: str
    scopes: list[str]
    device_flow: bool
    loopback_redirect: str


class CliExchangeRequest(ZModel):
    gitlab_access_token: str | None = None
    id_token: str | None = None
    device_name: str
    dev: bool | None = None


class CliToken(ZModel):
    token: str
    expires_at: datetime
    user: User


class LocalLoginRequest(ZModel):
    username: str
    password: str
    totp: str | None = None


class StepUpRequest(ZModel):
    return_to: str | None = None
    password: str | None = None
    totp: str | None = None


class StepUpResponse(ZModel):
    redirect_url: str | None = None
    step_up_at: datetime | None = None


class LoginRedirect(ZModel):
    redirect_url: str


class UserPatch(ZModel):
    action: Literal["suspend", "reactivate"]
    reason: str | None = None


class AuditEvent(ZModel):
    seq: int
    ts: datetime
    actor: str
    actor_ip: str | None = None
    user_agent: str | None = None
    session_id: str | None = None
    action: str
    target_type: str
    target_id: str | None = None
    team: str | None = None
    result: Literal["success", "denied", "error"]
    runs_as: str | None = None
    before: Any = None
    after: Any = None
    request_id: str | None = None
    prev_hash: str
    hash: str


class AuditCheckpoint(ZModel):
    seq: int
    hash: str
    signature: str
    ts: datetime
    key_id: str | None = None


class AuditVerify(ZModel):
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    checkpoints_verified: int
    detail: str | None = None


class AuditSettings(ZModel):
    retention_days: Annotated[int, Field(ge=90)]
    checkpoint_minutes: int
    forward: dict[str, Any]


NotificationKind = Literal[
    "approval_requested",
    "question_asked",
    "needs_attention",
    "mission_succeeded",
    "mission_failed",
    "budget_alert",
    "connector_unhealthy",
    "breakglass_used",
]


class NotificationDelivery(ZModel):
    last_sent_at: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None


class NotificationTarget(ZModel):
    """openapi NotificationTarget (ADR 0023). Webhook URLs and signing keys are secret references."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]
    name: str | None = None
    kind: Literal["slack", "mattermost", "teams", "webhook", "email"]
    enabled: bool = True
    events: list[NotificationKind]
    teams: list[str] = Field(default_factory=list)
    content_up_to: DataClass = DataClass.C1
    url_ref: str | None = None
    secret_ref: str | None = None
    to: list[str] = Field(default_factory=list)
    status: NotificationDelivery | None = None


class SmtpSettings(ZModel):
    host: str
    port: int | None = None
    starttls: bool = True
    username: str | None = None
    password_ref: str | None = None
    from_: Annotated[str, Field(alias="from")]


class NotificationSettings(ZModel):
    targets: list[NotificationTarget]
    smtp: SmtpSettings | None = None


class PersonalConnectionInfo(ZModel):
    """openapi PersonalConnection."""

    id: Id
    connector: str
    status: Literal["active", "expired", "revoked", "disabled"]
    read_scopes: list[str]
    write_scopes: list[str]
    last_used_at: datetime | None = None
    created_at: datetime


class UserDetail(User):
    bindings: list[RoleBinding]
    sessions: list[AuthSession]
    tokens: list[ApiToken]
    connections: list[PersonalConnectionInfo]
    recent_activity: list[AuditEvent]


class Guardrail(ZModel):
    value: Any
    locked: bool
    description: str | None = None


class HealthCheck(ZModel):
    ok: bool
    detail: str | None = None


class Health(ZModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    checks: dict[str, HealthCheck] | None = None


__all__ = ["Handle"]
