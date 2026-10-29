"""Admin area (docs/12 §3): users, role bindings, roles, group mapping, sign-in, sessions, tokens, teams,
guardrails, audit settings. Every write shows up in the audit log with before/after and needs step-up
(enforced by the contract, see x-step-up)."""

from __future__ import annotations

from typing import Any

from fastapi import Response
from sqlalchemy import select
from z_common.models import api_identity as m

from z_server import rbac
from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.api.paging import page
from z_server.api.views import audit_out, session_out, token_out, user_out
from z_server.auth import identity
from z_server.auth.oidc import OIDCClient
from z_server.db.models import (
    ApiToken,
    AuditEvent,
    AuthSession,
    PersonalConnection,
    Role,
    RoleBinding,
    Setting,
    Team,
    User,
)
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.state import AUTH_SETTINGS_KEY, SECRET_AAD

router = ContractRouter()
GUARDRAILS_KEY = "guardrails"
AUDIT_SETTINGS_KEY = "audit_settings"


async def _user(ctx: Ctx, user_id: str) -> User:
    user = await ctx.db.get(User, user_id)
    if user is None:
        user = (await ctx.db.execute(select(User).where(User.username == user_id))).scalar_one_or_none()
    if user is None:
        raise Problem("not-found", "no such user")
    return user


# ---------------------------------------------------------------- users


@router.operation("adminListUsers")
async def admin_list_users(
    ctx: CtxDep,
    cursor: str | None = None,
    limit: int = 50,
    role: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    stmt = select(User)
    if status:
        stmt = stmt.where(User.status == status)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(User.username.ilike(like) | User.name.ilike(like) | User.email.ilike(like))
    rows, nxt = await page(ctx.db, stmt, User.id, cursor, limit)
    items = [await user_out(ctx.db, ctx.state, u) for u in rows]
    if role:
        items = [u for u in items if any(r.role == role for r in u.effective_roles)]
    return {"items": [i.dump() for i in items], "next_cursor": nxt}


@router.operation("adminGetUser")
async def admin_get_user(ctx: CtxDep, id: str) -> m.UserDetail:
    user = await _user(ctx, id)
    base = await user_out(ctx.db, ctx.state, user)
    bindings = (await ctx.db.execute(select(RoleBinding).where(RoleBinding.subject == f"user:{user.id}"))).scalars()
    sessions = (
        await ctx.db.execute(
            select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        )
    ).scalars()
    tokens = (await ctx.db.execute(select(ApiToken).where(ApiToken.user_id == user.id))).scalars()
    connections = (
        await ctx.db.execute(select(PersonalConnection).where(PersonalConnection.user_id == user.id))
    ).scalars()
    activity = (
        await ctx.db.execute(
            select(AuditEvent).where(AuditEvent.actor == user.username).order_by(AuditEvent.seq.desc()).limit(25)
        )
    ).scalars()
    return m.UserDetail(
        **base.model_dump(),
        bindings=[_binding_out(b) for b in bindings],
        sessions=[session_out(s, user.username) for s in sessions if s.expires_at > identity.now()],
        tokens=[token_out(t, user.username) for t in tokens],
        connections=[
            m.PersonalConnectionInfo(
                id=c.id,
                connector=c.connector_id,
                status=c.status,
                read_scopes=c.read_scopes,
                write_scopes=c.write_scopes,
                last_used_at=c.last_used_at,
                created_at=c.created_at,
            )
            for c in connections
        ],
        recent_activity=[audit_out(a) for a in activity],
    )


@router.operation("adminPatchUser")
async def admin_patch_user(ctx: CtxDep, id: str, body: m.UserPatch) -> m.User:
    user = await _user(ctx, id)
    if user.id == ctx.user.user_id:
        raise Problem("separation-of-duties", "you cannot suspend or reactivate yourself")
    before = user.status
    if body.action == "suspend":
        user.status = "suspended"
        await identity.revoke_user_access(ctx.db, user.id, f"suspended by {ctx.user.username}")
    else:
        user.status = "active"
    await ctx.audit(
        f"admin.user_{body.action}",
        "user",
        user.id,
        before={"status": before},
        after={"status": user.status, "reason": body.reason},
    )
    return await user_out(ctx.db, ctx.state, user)


@router.operation("adminRevokeUserSessions", status_code=204)
async def admin_revoke_user_sessions(ctx: CtxDep, id: str, body: dict[str, Any] | None = None) -> Response:
    user = await _user(ctx, id)
    count = await identity.revoke_user_access(ctx.db, user.id, f"revoked by {ctx.user.username}")
    await ctx.audit(
        "admin.sessions_revoked", "user", user.id, after={"sessions": count, "reason": (body or {}).get("reason")}
    )
    return Response(status_code=204)


# ---------------------------------------------------------------- role bindings and roles


def _binding_out(b: RoleBinding) -> m.RoleBinding:
    return m.RoleBinding(
        id=b.id,
        subject=b.subject,
        role=b.role,
        scope=b.scope,
        source=b.source,
        expires_at=b.expires_at,
        created_by=b.created_by,
        created_at=b.created_at,
    )


async def _known_roles(ctx: Ctx) -> set[str]:
    return set(rbac.builtin_roles()) | set(await identity.custom_roles(ctx.db))


@router.operation("adminListBindings")
async def admin_list_bindings(ctx: CtxDep, subject: str | None = None, role: str | None = None) -> list[m.RoleBinding]:
    stmt = select(RoleBinding)
    if subject:
        stmt = stmt.where(RoleBinding.subject == subject)
    if role:
        stmt = stmt.where(RoleBinding.role == role)
    return [_binding_out(b) for b in (await ctx.db.execute(stmt.order_by(RoleBinding.created_at))).scalars()]


@router.operation("adminCreateBinding", status_code=201)
async def admin_create_binding(ctx: CtxDep, body: m.RoleBindingWrite) -> m.RoleBinding:
    if body.subject == f"user:{ctx.user.user_id}":
        raise Problem("separation-of-duties", "an admin cannot change their own roles; ask another admin")
    if body.role not in await _known_roles(ctx):
        raise Problem("validation", f"unknown role {body.role}")
    if body.subject.startswith("user:") and await ctx.db.get(User, body.subject.removeprefix("user:")) is None:
        raise Problem("validation", "unknown user")
    if body.scope.startswith("team:") and await ctx.db.get(Team, body.scope.removeprefix("team:")) is None:
        raise Problem("validation", "unknown team")
    if body.expires_at is not None and body.expires_at <= identity.now():
        raise Problem("validation", "expires_at is in the past")
    row = RoleBinding(
        id=new_id("rb"),
        subject=body.subject,
        role=body.role,
        scope=body.scope,
        source="manual",
        expires_at=body.expires_at,
        reason=body.reason,
        created_by=ctx.user.username,
    )
    ctx.db.add(row)
    await ctx.db.flush()
    out = _binding_out(row)
    await ctx.audit("admin.role_granted", "role_binding", row.id, after=out.dump())
    return out


@router.operation("adminDeleteBinding", status_code=204)
async def admin_delete_binding(ctx: CtxDep, id: str) -> Response:
    row = await ctx.db.get(RoleBinding, id)
    if row is None:
        raise Problem("not-found")
    if row.source != "manual":
        raise Problem("conflict", "GitLab-derived roles change in GitLab or in the group mapping")
    before = _binding_out(row).dump()
    await ctx.db.delete(row)
    await ctx.audit("admin.role_revoked", "role_binding", id, before=before)
    return Response(status_code=204)


def _role_out(
    name: str,
    permissions: frozenset[str] | list[str],
    builtin: bool,
    rid: str | None = None,
    description: str | None = None,
) -> m.Role:
    return m.Role(
        id=rid or f"builtin:{name}",
        name=name,
        builtin=builtin,
        description=description,
        permissions=sorted(permissions),
    )


@router.operation("adminListRoles")
async def admin_list_roles(ctx: CtxDep) -> list[m.Role]:
    out = [_role_out(n, p, True) for n, p in rbac.builtin_roles().items()]
    for row in (await ctx.db.execute(select(Role).where(Role.builtin.is_(False)).order_by(Role.name))).scalars():
        out.append(_role_out(row.name, list(row.permissions), False, row.id, row.description))
    return out


@router.operation("adminCreateRole", status_code=201)
async def admin_create_role(ctx: CtxDep, body: m.RoleWrite) -> m.Role:
    if body.name in rbac.builtin_roles():
        raise Problem("conflict", "built-in roles are read-only")
    unknown = sorted(set(body.permissions) - set(rbac.catalog()))
    if unknown:
        raise Problem("validation", f"unknown permissions: {', '.join(unknown)}")
    if (await ctx.db.execute(select(Role).where(Role.name == body.name))).scalar_one_or_none():
        raise Problem("conflict", "a role with this name exists")
    row = Role(
        id=new_id("rol"),
        name=body.name,
        builtin=False,
        description=body.description,
        permissions=sorted(set(body.permissions)),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    out = _role_out(row.name, row.permissions, False, row.id, row.description)
    await ctx.audit("admin.role_created", "role", row.id, after=out.dump())
    return out


@router.operation("adminPatchRole")
async def admin_patch_role(ctx: CtxDep, id: str, body: m.RoleWrite) -> m.Role:
    row = await ctx.db.get(Role, id)
    if row is None or row.builtin:
        raise Problem("not-found" if row is None else "conflict", "built-in roles are read-only" if row else None)
    unknown = sorted(set(body.permissions) - set(rbac.catalog()))
    if unknown:
        raise Problem("validation", f"unknown permissions: {', '.join(unknown)}")
    before = _role_out(row.name, row.permissions, False, row.id, row.description).dump()
    row.description, row.permissions = body.description, sorted(set(body.permissions))
    out = _role_out(row.name, row.permissions, False, row.id, row.description)
    await ctx.audit("admin.role_updated", "role", row.id, before=before, after=out.dump())
    return out


@router.operation("adminListPermissions")
async def admin_list_permissions(ctx: CtxDep) -> list[m.PermissionInfo]:
    roles = rbac.builtin_roles()
    return [
        m.PermissionInfo(permission=p, description=d, roles=sorted(r for r, perms in roles.items() if p in perms))
        for p, d in rbac.catalog().items()
    ]


@router.operation("adminWhoCan")
async def admin_who_can(ctx: CtxDep, permission: str) -> m.WhoCan:
    if permission not in rbac.catalog():
        raise Problem("validation", "unknown permission")
    entries = []
    custom = await identity.custom_roles(ctx.db)
    for user in (await ctx.db.execute(select(User).where(User.status == "active"))).scalars():
        roles, _ = await identity.roles_for_user(ctx.db, user, ctx.state.config_auth)
        for r in roles:
            if permission in rbac.role_permissions(r.role, custom):
                entries.append(m.WhoCanEntry(user=user.username, scope=r.scope, via=f"{r.role} ({r.via or r.source})"))
    return m.WhoCan(permission=permission, users=entries)


# ---------------------------------------------------------------- group mapping


@router.operation("adminGetGroupMapping")
async def admin_get_group_mapping(ctx: CtxDep) -> m.GroupMappingConfig:
    return m.GroupMappingConfig.model_validate(await identity.effective_mapping(ctx.db, ctx.state.config_auth))


def _validate_mapping(body: m.GroupMappingConfig, known_roles: set[str]) -> None:
    unknown = sorted({g.role for g in body.group_mapping} - known_roles)
    if unknown:
        raise Problem("validation", f"unknown roles: {', '.join(unknown)}")
    if not any(g.role == "admin" and g.scope == "org" for g in body.group_mapping):
        raise Problem("validation", "the mapping must keep at least one org-wide admin group")


async def _dry_run(ctx: Ctx, new: dict[str, Any]) -> m.GroupMappingDryRun:
    current = await identity.effective_mapping(ctx.db, ctx.state.config_auth)
    teams = await identity.team_ids_by_name(ctx.db)
    for t in new.get("team_mapping", []):
        teams.setdefault(t["team"], f"(new team {t['team']})")
    names = {v: k for k, v in teams.items()}
    changes: list[m.AccessChange] = []
    denied: list[str] = []
    users = (
        (await ctx.db.execute(select(User).where(User.status == "active", User.local_kind.is_(None)))).scalars().all()
    )
    for user in users:
        groups = list(user.groups or [])
        if not rbac.in_allowed_groups(groups, new.get("allowed_groups", [])):
            denied.append(user.username)
        before, _ = rbac.map_groups(groups, current, teams)
        after, _ = rbac.map_groups(groups, new, teams)
        b = {(r.role, r.scope) for r in before}
        a = {(r.role, r.scope) for r in after}
        for role, scope in sorted(a - b):
            changes.append(
                m.AccessChange(
                    user=user.username,
                    role=role,
                    scope=scope,
                    team=names.get(scope.removeprefix("team:")),
                    change="gain",
                )
            )
        for role, scope in sorted(b - a):
            changes.append(
                m.AccessChange(
                    user=user.username,
                    role=role,
                    scope=scope,
                    team=names.get(scope.removeprefix("team:")),
                    change="loss",
                )
            )
    return m.GroupMappingDryRun(changes=changes, users_evaluated=len(users), denied_users=denied)


@router.operation("adminDryRunGroupMapping")
async def admin_dry_run_group_mapping(ctx: CtxDep, body: m.GroupMappingConfig) -> m.GroupMappingDryRun:
    _validate_mapping(body, await _known_roles(ctx))
    return await _dry_run(ctx, body.dump())


@router.operation("adminPutGroupMapping")
async def admin_put_group_mapping(ctx: CtxDep, body: m.GroupMappingConfig) -> m.GroupMappingConfig:
    _validate_mapping(body, await _known_roles(ctx))
    before = await identity.effective_mapping(ctx.db, ctx.state.config_auth)
    preview = await _dry_run(ctx, body.dump())
    row = await ctx.db.get(Setting, identity.GROUP_MAPPING_KEY)
    if row is None:
        ctx.db.add(Setting(key=identity.GROUP_MAPPING_KEY, value=body.dump(), updated_by=ctx.user.username))
    else:
        row.value, row.updated_by = body.dump(), ctx.user.username
    await identity.ensure_teams(ctx.db, body.dump())
    await ctx.audit(
        "admin.group_mapping_changed",
        "group_mapping",
        None,
        before=before,
        after={**body.dump(), "dry_run": preview.dump()},
    )
    return body


# ---------------------------------------------------------------- sign-in settings


async def _auth_settings_out(ctx: Ctx) -> m.AuthSettings:
    auth = await ctx.state.auth_settings(ctx.db)
    from z_server.auth.local import BOOTSTRAP_NAME
    from z_server.db.models import LocalAccount

    accounts = {a.name: a for a in (await ctx.db.execute(select(LocalAccount))).scalars()}
    boot = accounts.get(BOOTSTRAP_NAME)
    state = "disabled"
    if boot is not None and boot.enabled:
        state = "expired" if boot.expires_at and boot.expires_at <= identity.now() else "active"
    jwks = None
    if auth.get("issuer_url") and ctx.state.settings.auth_mode == "oidc":
        ok, keys, error = await (await ctx.state.oidc(ctx.db)).jwks_status()
        jwks = m.JwksStatus(ok=ok, keys=keys, fetched_at=identity.now() if ok else None, error=error)
    return m.AuthSettings(
        mode=ctx.state.settings.auth_mode,
        issuer_url=str(auth.get("issuer_url", "")),
        client_id=str(auth.get("client_id", "")),
        cli_client_id=auth.get("cli_client_id"),
        scopes=list(auth.get("scopes", [])),
        groups_claim=str(auth.get("groups_claim", "groups")),
        session=m.SessionLifetimes.model_validate(auth.get("session", {})),
        step_up_max_age_s=int(auth.get("step_up_max_age_s", 300)),
        jwks=jwks,
        local_accounts=m.LocalAccountsState(
            bootstrap=m.BootstrapState(
                enabled=bool(boot and boot.enabled), state=state, expires_at=boot.expires_at if boot else None
            ),
            test_accounts=[
                m.TestAccountInfo(name=a.name, role=a.role or "", team=a.team, expires_at=a.expires_at)
                for a in accounts.values()
                if a.kind == "test" and a.enabled
            ],
            breakglass=m.BreakglassState(enabled=any(a.kind == "breakglass" and a.enabled for a in accounts.values())),
        ),
    )


@router.operation("adminGetAuthSettings")
async def admin_get_auth_settings(ctx: CtxDep) -> m.AuthSettings:
    return await _auth_settings_out(ctx)


@router.operation("adminPutAuthSettings")
async def admin_put_auth_settings(ctx: CtxDep, body: m.AuthSettings) -> m.AuthSettings:
    from z_server.security import check_outbound_url

    await check_outbound_url(body.issuer_url, ctx.state)
    before = (await _auth_settings_out(ctx)).dump()
    value: dict[str, Any] = {
        "issuer_url": body.issuer_url.rstrip("/"),
        "client_id": body.client_id,
        "scopes": body.scopes,
        "groups_claim": body.groups_claim,
    }
    if body.cli_client_id:
        value["cli_client_id"] = body.cli_client_id
    if body.session:
        value["session"] = body.session.dump()
    if body.step_up_max_age_s:
        value["step_up_max_age_s"] = body.step_up_max_age_s
    row = await ctx.db.get(Setting, AUTH_SETTINGS_KEY)
    if body.client_secret:
        value["client_secret_sealed"] = ctx.state.sealer.seal(body.client_secret, SECRET_AAD).hex()
    elif row is not None and row.value.get("client_secret_sealed"):
        value["client_secret_sealed"] = row.value["client_secret_sealed"]
    if row is None:
        ctx.db.add(Setting(key=AUTH_SETTINGS_KEY, value=value, updated_by=ctx.user.username))
    else:
        row.value, row.updated_by = value, ctx.user.username
    await ctx.db.flush()
    after = (await _auth_settings_out(ctx)).dump()
    await ctx.audit(
        "admin.auth_settings_changed",
        "auth_settings",
        None,
        before=before,
        after={**after, "client_secret_changed": bool(body.client_secret)},
    )
    return await _auth_settings_out(ctx)


@router.operation("adminTestAuthSettings")
async def admin_test_auth_settings(ctx: CtxDep, body: dict[str, Any] | None = None) -> m.AuthSettingsTest:
    auth = await ctx.state.auth_settings(ctx.db)
    if not auth.get("issuer_url"):
        return m.AuthSettingsTest(ok=False, discovery=False, jwks=False, detail="issuer_url is not set")
    client = OIDCClient(str(auth["issuer_url"]), str(auth.get("client_id", "")), "", ctx.state.http)
    try:
        await client.discovery()
    except Exception as exc:  # noqa: BLE001 — reported to the admin
        return m.AuthSettingsTest(ok=False, discovery=False, jwks=False, detail=str(exc)[:300])
    ok, keys, error = await client.jwks_status()
    return m.AuthSettingsTest(ok=ok, discovery=True, jwks=ok, detail=f"{keys} signing key(s)" if ok else str(error))


# ---------------------------------------------------------------- sessions and tokens


@router.operation("adminListSessions")
async def admin_list_sessions(
    ctx: CtxDep, cursor: str | None = None, limit: int = 50, user: str | None = None
) -> dict[str, Any]:
    stmt = select(AuthSession).where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > identity.now())
    if user:
        stmt = stmt.where(AuthSession.user_id == (await _user(ctx, user)).id)
    rows, nxt = await page(ctx.db, stmt, AuthSession.id, cursor, limit)
    names = {
        u.id: u.username
        for u in (await ctx.db.execute(select(User).where(User.id.in_({r.user_id for r in rows})))).scalars()
    }
    return {"items": [session_out(r, names.get(r.user_id, "?")).dump() for r in rows], "next_cursor": nxt}


@router.operation("adminRevokeSession", status_code=204)
async def admin_revoke_session(ctx: CtxDep, id: str) -> Response:
    row = await ctx.db.get(AuthSession, id)
    if row is None:
        raise Problem("not-found")
    row.revoked_at, row.revoked_reason = identity.now(), f"revoked by {ctx.user.username}"
    await ctx.audit("admin.session_revoked", "session", id, after={"user_id": row.user_id})
    return Response(status_code=204)


@router.operation("adminListTokens")
async def admin_list_tokens(
    ctx: CtxDep, cursor: str | None = None, limit: int = 50, user: str | None = None
) -> dict[str, Any]:
    stmt = select(ApiToken)
    if user:
        stmt = stmt.where(ApiToken.user_id == (await _user(ctx, user)).id)
    rows, nxt = await page(ctx.db, stmt, ApiToken.id, cursor, limit)
    names = {
        u.id: u.username
        for u in (await ctx.db.execute(select(User).where(User.id.in_({r.user_id for r in rows})))).scalars()
    }
    return {"items": [token_out(r, names.get(r.user_id, "?")).dump() for r in rows], "next_cursor": nxt}


@router.operation("adminRevokeToken", status_code=204)
async def admin_revoke_token(ctx: CtxDep, id: str) -> Response:
    row = await ctx.db.get(ApiToken, id)
    if row is None:
        raise Problem("not-found")
    row.revoked_at = identity.now()
    await ctx.audit("admin.api_token_revoked", "api_token", id, after={"user_id": row.user_id})
    return Response(status_code=204)


# ---------------------------------------------------------------- teams


async def _team_out(ctx: Ctx, team: Team) -> m.Team:
    from sqlalchemy import func

    from z_server.db.models import UserTeam

    count = (
        await ctx.db.execute(select(func.count()).select_from(UserTeam).where(UserTeam.team_id == team.id))
    ).scalar_one()
    return m.Team(
        id=team.id,
        name=team.name,
        gitlab_groups=list(team.gitlab_groups),
        members_count=int(count),
        monthly_token_budget=team.monthly_token_budget,
        created_at=team.created_at,
    )


@router.operation("listTeams")
async def list_teams(ctx: CtxDep) -> list[m.Team]:
    return [await _team_out(ctx, t) for t in (await ctx.db.execute(select(Team).order_by(Team.name))).scalars()]


@router.operation("createTeam", status_code=201)
async def create_team(ctx: CtxDep, body: m.TeamWrite) -> m.Team:
    if (await ctx.db.execute(select(Team).where(Team.name == body.name))).scalar_one_or_none():
        raise Problem("conflict", "a team with this name exists")
    team = Team(id=new_id("team"), name=body.name, monthly_token_budget=body.monthly_token_budget, gitlab_groups=[])
    ctx.db.add(team)
    await ctx.db.flush()
    out = await _team_out(ctx, team)
    await ctx.audit("admin.team_created", "team", team.id, after=out.dump())
    return out


@router.operation("getTeam")
async def get_team(ctx: CtxDep, id: str) -> m.Team:
    team = await ctx.db.get(Team, id)
    if team is None:
        raise Problem("not-found")
    return await _team_out(ctx, team)


@router.operation("patchTeam")
async def patch_team(ctx: CtxDep, id: str, body: m.TeamWrite) -> m.Team:
    team = await ctx.db.get(Team, id)
    if team is None:
        raise Problem("not-found")
    before = (await _team_out(ctx, team)).dump()
    team.name, team.monthly_token_budget = body.name, body.monthly_token_budget
    out = await _team_out(ctx, team)
    await ctx.audit("admin.team_updated", "team", team.id, before=before, after=out.dump())
    return out


# ---------------------------------------------------------------- guardrails and audit settings


GUARDRAIL_TEXT = {
    "human_approval_for_writes": "Every external write needs a human approval",
    "two_approvers_for_destructive": "Destructive actions need two distinct approvers",
    "sandbox_network": "Runners have no network except Z and the package mirrors",
    "judge_injection_scan": "The judge model double-checks tool results and staged inputs (the pattern scan always runs)",
    "learning_requires_approval": "Learning proposals are applied only by a human",
    "max_steps_per_job": "Hard step limit per job",
    "max_job_minutes": "Hard wall time per job",
}


async def guardrails(ctx: Ctx) -> dict[str, dict[str, Any]]:
    configured: dict[str, Any] = dict(ctx.state.config.get("guardrails", {}))
    row = await ctx.db.get(Setting, GUARDRAILS_KEY)
    overrides = row.value if row else {}
    out = {}
    for name, value in configured.items():
        locked = bool(value.get("locked")) if isinstance(value, dict) else False
        current = value.get("value") if isinstance(value, dict) else value
        if not locked and name in overrides:
            current = overrides[name]
        out[name] = {"value": current, "locked": locked, "description": GUARDRAIL_TEXT.get(name, name)}
    return out


@router.operation("getGuardrails")
async def get_guardrails(ctx: CtxDep) -> dict[str, Any]:
    return await guardrails(ctx)


@router.operation("putGuardrails")
async def put_guardrails(ctx: CtxDep, body: dict[str, m.Guardrail]) -> dict[str, Any]:
    current = await guardrails(ctx)
    changes: dict[str, Any] = {}
    for name, item in body.items():
        if name not in current:
            raise Problem("validation", f"unknown guardrail {name}")
        if current[name]["value"] == item.value:
            continue
        if current[name]["locked"]:
            raise Problem("locked", f"{name} is locked by the platform")
        if name in {"max_steps_per_job", "max_job_minutes"} and (not isinstance(item.value, int) or item.value < 1):
            raise Problem("validation", f"{name} must be a positive integer")
        if name == "judge_injection_scan" and not isinstance(item.value, bool):
            raise Problem("validation", f"{name} must be true or false")
        changes[name] = item.value
    if changes:
        row = await ctx.db.get(Setting, GUARDRAILS_KEY)
        merged = {**(row.value if row else {}), **changes}
        if row is None:
            ctx.db.add(Setting(key=GUARDRAILS_KEY, value=merged, updated_by=ctx.user.username))
        else:
            row.value, row.updated_by = merged, ctx.user.username
        await ctx.db.flush()
    after = await guardrails(ctx)
    await ctx.audit(
        "admin.guardrails_changed", "guardrails", None, before={k: current[k]["value"] for k in changes}, after=changes
    )
    return after


async def audit_settings(ctx: Ctx) -> m.AuditSettings:
    cfg = ctx.state.config.get("audit", {})
    row = await ctx.db.get(Setting, AUDIT_SETTINGS_KEY)
    value = {
        "retention_days": cfg.get("retention_days", 365),
        "checkpoint_minutes": cfg.get("checkpoint_minutes", 60),
        "forward": cfg.get("forward", {}),
    }
    if row is not None:
        value.update(row.value)
    return m.AuditSettings.model_validate(value)


@router.operation("adminGetAuditSettings")
async def admin_get_audit_settings(ctx: CtxDep) -> m.AuditSettings:
    return await audit_settings(ctx)


@router.operation("adminPutAuditSettings")
async def admin_put_audit_settings(ctx: CtxDep, body: m.AuditSettings) -> m.AuditSettings:
    from z_server.security import check_outbound_url

    for target in body.forward.values():
        if isinstance(target, dict) and target.get("enabled") and target.get("url"):
            await check_outbound_url(str(target["url"]), ctx.state)
    before = (await audit_settings(ctx)).dump()
    row = await ctx.db.get(Setting, AUDIT_SETTINGS_KEY)
    if row is None:
        ctx.db.add(Setting(key=AUDIT_SETTINGS_KEY, value=body.dump(), updated_by=ctx.user.username))
    else:
        row.value, row.updated_by = body.dump(), ctx.user.username
    await ctx.audit("admin.audit_settings_changed", "audit_settings", None, before=before, after=body.dump())
    return body
