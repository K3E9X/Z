"""Users, sessions and principals (docs/12 §1-2).

- Users are keyed by issuer + sub; GitLab group paths are stored at each evaluation.
- Sessions are server-side; only the SHA-256 of the bearer value is stored.
- A Principal's grants come from the group mapping (source gitlab) and manual bindings (source manual).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from z_server import rbac
from z_server.crypto import new_token, token_hash
from z_server.db.models import (
    ApiToken,
    AuthSession,
    Credential,
    LocalAccount,
    PersonalConnection,
    Role,
    RoleBinding,
    Setting,
    Team,
    User,
    UserTeam,
)
from z_server.ids import new_id

GROUP_MAPPING_KEY = "group_mapping"
SESSION_PREFIX = {"web": "zw", "cli": "zs"}
API_TOKEN_PREFIX = "zt"  # noqa: S105 (token prefix, not a secret)
BOOTSTRAP_OPERATIONS = frozenset(
    {
        "getMe",
        "authLogout",
        "authStepUp",
        "adminGetAuthSettings",
        "adminPutAuthSettings",
        "adminTestAuthSettings",
        "adminGetGroupMapping",
        "adminPutGroupMapping",
        "adminDryRunGroupMapping",
        "adminListPermissions",
        "adminListRoles",
        "listTeams",
        "createTeam",
        "globalStream",
    }
)
BOOTSTRAP_PERMISSIONS = frozenset({"users:manage", "teams:read", "teams:manage", "guardrails:read"})


def now() -> datetime:
    return datetime.now(UTC)


async def effective_mapping(db: AsyncSession, config_auth: dict[str, Any]) -> dict[str, Any]:
    """Group mapping from the database (admin edits) or from config/z.yaml."""
    row = await db.get(Setting, GROUP_MAPPING_KEY)
    if row is not None:
        return dict(row.value)
    return {
        "allowed_groups": list(config_auth.get("allowed_groups", [])),
        "no_match": config_auth.get("no_match", "viewer"),
        "group_mapping": list(config_auth.get("group_mapping", [])),
        "team_mapping": list(config_auth.get("team_mapping", [])),
    }


async def team_ids_by_name(db: AsyncSession) -> dict[str, str]:
    return {t.name: t.id for t in (await db.execute(select(Team))).scalars()}


async def ensure_teams(db: AsyncSession, mapping: dict[str, Any]) -> None:
    existing = await team_ids_by_name(db)
    for m in mapping.get("team_mapping", []):
        if m["team"] not in existing:
            team = Team(id=new_id("team"), name=m["team"], gitlab_groups=[m["group"]])
            db.add(team)
            existing[m["team"]] = team.id
    await db.flush()


async def custom_roles(db: AsyncSession) -> dict[str, list[str]]:
    return {
        r.name: list(r.permissions) for r in (await db.execute(select(Role).where(Role.builtin.is_(False)))).scalars()
    }


async def manual_assignments(db: AsyncSession, user_id: str, at: datetime) -> list[rbac.RoleAssignment]:
    rows = (
        await db.execute(
            select(RoleBinding).where(RoleBinding.subject == f"user:{user_id}", RoleBinding.source == "manual")
        )
    ).scalars()
    return [
        rbac.RoleAssignment(r.role, r.scope, "manual", None, r.expires_at)
        for r in rows
        if r.expires_at is None or r.expires_at > at
    ]


async def roles_for_user(
    db: AsyncSession, user: User, config_auth: dict[str, Any]
) -> tuple[list[rbac.RoleAssignment], set[str]]:
    """Role assignments and team ids of a user right now."""
    at = now()
    if user.local_kind in {"bootstrap", "test", "breakglass", "dev"}:
        account = (await db.execute(select(LocalAccount).where(LocalAccount.user_id == user.id))).scalar_one_or_none()
        teams = {t for (t,) in (await db.execute(select(UserTeam.team_id).where(UserTeam.user_id == user.id))).all()}
        if user.local_kind == "breakglass":
            return [rbac.RoleAssignment("admin", rbac.ORG, "manual", "breakglass")], teams
        if user.local_kind == "dev":
            role = str(config_auth.get("dev_user", {}).get("role", "admin"))
            return [rbac.RoleAssignment(role, rbac.ORG, "manual", "dev")], teams
        if user.local_kind == "test" and account and account.role:
            scope = f"team:{next(iter(teams))}" if teams else rbac.ORG
            return [rbac.RoleAssignment(account.role, scope, "manual", "test account")], teams
        return [], teams
    mapping = await effective_mapping(db, config_auth)
    mapped, teams = rbac.map_groups(user.groups or [], mapping, await team_ids_by_name(db))
    return mapped + await manual_assignments(db, user.id, at), teams


async def build_principal(
    db: AsyncSession,
    user: User,
    config_auth: dict[str, Any],
    *,
    kind: str,
    session: AuthSession | None = None,
    token_scopes: list[str] | None = None,
) -> rbac.Principal:
    roles, teams = await roles_for_user(db, user, config_auth)
    grants = rbac.grants_for(roles, await custom_roles(db), now())
    if user.local_kind == "bootstrap":
        grants = frozenset(rbac.Grant(p, rbac.ORG) for p in BOOTSTRAP_PERMISSIONS)
    if token_scopes is not None:
        grants = rbac.restrict(grants, token_scopes)
    return rbac.Principal(
        user_id=user.id,
        username=user.username,
        kind=kind,
        session_id=session.id if session else None,
        session_kind=session.kind if session else None,
        teams=frozenset(teams),
        roles=roles,
        grants=grants,
        step_up_at=session.step_up_at if session else None,
        local_kind=user.local_kind,
        csrf_token=session.csrf_token if session else None,
    )


# ---------------------------------------------------------------- sessions and tokens


@dataclass
class Lifetimes:
    idle: timedelta
    absolute: timedelta
    cli: timedelta

    @classmethod
    def from_config(cls, config_auth: dict[str, Any]) -> Lifetimes:
        s = config_auth.get("session", {})
        return cls(
            idle=timedelta(minutes=int(s.get("idle_minutes", 120))),
            absolute=timedelta(hours=int(s.get("absolute_hours", 12))),
            cli=timedelta(days=int(s.get("cli_token_days", 30))),
        )


async def create_session(
    db: AsyncSession,
    user: User,
    kind: str,
    lifetimes: Lifetimes,
    *,
    device: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    step_up: bool = False,
) -> tuple[str, AuthSession]:
    token = new_token(SESSION_PREFIX[kind])
    at = now()
    row = AuthSession(
        id=new_id("ses"),
        user_id=user.id,
        kind=kind,
        token_hash=token_hash(token),
        csrf_token=new_token("csrf", 24) if kind == "web" else None,
        device=device,
        ip=ip,
        user_agent=user_agent,
        created_at=at,
        last_seen_at=at,
        expires_at=at + (lifetimes.absolute if kind == "web" else lifetimes.cli),
        step_up_at=at if step_up else None,
    )
    db.add(row)
    await db.flush()
    return token, row


async def resolve_session(db: AsyncSession, token: str, lifetimes: Lifetimes) -> tuple[AuthSession, User] | None:
    row = (
        await db.execute(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    ).scalar_one_or_none()
    at = now()
    if row is None or row.revoked_at is not None or row.expires_at <= at:
        return None
    if row.kind == "web" and row.last_seen_at + lifetimes.idle <= at:
        row.revoked_at, row.revoked_reason = at, "idle timeout"
        return None
    user = await db.get(User, row.user_id)
    if user is None or user.status != "active":
        return None
    if (at - row.last_seen_at).total_seconds() > 30:
        row.last_seen_at = at
    return row, user


async def resolve_api_token(db: AsyncSession, token: str) -> tuple[ApiToken, User] | None:
    row = (await db.execute(select(ApiToken).where(ApiToken.token_hash == token_hash(token)))).scalar_one_or_none()
    at = now()
    if row is None or row.revoked_at is not None or row.expires_at <= at:
        return None
    user = await db.get(User, row.user_id)
    if user is None or user.status != "active":
        return None
    if row.last_used_at is None or (at - row.last_used_at).total_seconds() > 60:
        row.last_used_at = at
    return row, user


async def revoke_user_access(db: AsyncSession, user_id: str, reason: str) -> int:
    """Revoke every session, CLI token and API token of a user, and their personal connections (docs/12 §1
    Lifecycle: the sealed delegated grants are deleted, so running missions cannot act as them any more).
    Returns the number of sessions revoked."""
    at = now()
    for conn in (
        await db.execute(
            select(PersonalConnection)
            .where(PersonalConnection.user_id == user_id, PersonalConnection.status != "revoked")
            .with_for_update()
        )
    ).scalars():
        for cred_id in (conn.read_credential_id, conn.write_credential_id):
            cred = await db.get(Credential, cred_id) if cred_id else None
            if cred is not None:
                await db.delete(cred)
        conn.read_credential_id = conn.write_credential_id = None
        conn.status = "revoked"
    result = await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=at, revoked_reason=reason)
    )
    await db.execute(
        update(ApiToken).where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None)).values(revoked_at=at)
    )
    return int(getattr(result, "rowcount", 0) or 0)
