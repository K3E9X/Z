"""Provisioning and hourly re-evaluation of GitLab users (docs/12 §1 "Lifecycle")."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from z_server import rbac
from z_server.auth.identity import effective_mapping, now, revoke_user_access, team_ids_by_name
from z_server.auth.oidc import OIDCClient, OIDCError
from z_server.crypto import Sealer
from z_server.db.models import LocalAccount, User, UserTeam
from z_server.ids import new_id

REFRESH_AAD = "gitlab-refresh"


@dataclass
class SignInResult:
    user: User
    roles: list[rbac.RoleAssignment]
    created: bool
    is_admin: bool


def groups_from(userinfo: dict[str, Any], claim: str) -> list[str]:
    value = userinfo.get(claim, [])
    return sorted({str(g) for g in value}) if isinstance(value, list) else []


async def _sync_teams(db: AsyncSession, user_id: str, teams: set[str]) -> None:
    await db.execute(delete(UserTeam).where(UserTeam.user_id == user_id, UserTeam.source == "gitlab"))
    for team_id in sorted(teams):
        db.add(UserTeam(user_id=user_id, team_id=team_id, source="gitlab"))


async def sign_in(
    db: AsyncSession,
    client: OIDCClient,
    config_auth: dict[str, Any],
    claims: dict[str, Any],
    tokens: dict[str, Any],
    sealer: Sealer,
) -> SignInResult:
    """Validate group membership and create or update the user. Raises OIDCError with a stable reason."""
    info = await client.userinfo(str(tokens["access_token"]))
    if info.get("sub") != claims["sub"]:
        raise OIDCError("subject_mismatch", "userinfo and ID token disagree")
    groups = groups_from(info, str(config_auth.get("groups_claim", "groups")))
    mapping = await effective_mapping(db, config_auth)
    if not rbac.in_allowed_groups(groups, mapping.get("allowed_groups", [])):
        raise OIDCError("not_in_allowed_group")
    roles, teams = rbac.map_groups(groups, mapping, await team_ids_by_name(db))
    if not roles:
        raise OIDCError("no_mapped_group")
    issuer = str(claims["iss"]).rstrip("/")
    user = (
        await db.execute(select(User).where(User.issuer == issuer, User.sub == str(claims["sub"])))
    ).scalar_one_or_none()
    created = user is None
    username = str(info.get("preferred_username") or claims.get("preferred_username") or claims["sub"])
    if user is None:
        clash = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if clash is not None:
            raise OIDCError("username_conflict", username)
        user = User(
            id=new_id("usr"), issuer=issuer, sub=str(claims["sub"]), username=username, name=username, status="active"
        )
        db.add(user)
    elif user.status == "suspended":
        raise OIDCError("account_suspended")
    at = now()
    user.username = username
    user.name = str(info.get("name") or claims.get("name") or username)
    user.email = info.get("email") or claims.get("email")
    user.avatar_url = info.get("picture")
    user.groups = groups
    user.status = "active"
    user.last_login_at = at
    user.last_reevaluated_at = at
    if tokens.get("refresh_token"):
        user.gitlab_refresh_token_enc = sealer.seal(str(tokens["refresh_token"]), REFRESH_AAD)
    await db.flush()
    await _sync_teams(db, user.id, teams)
    is_admin = any(r.role == "admin" and r.scope == rbac.ORG for r in roles)
    return SignInResult(user=user, roles=roles, created=created, is_admin=is_admin)


async def disable_bootstrap(db: AsyncSession) -> bool:
    """The bootstrap admin ends the first time a GitLab user mapped to admin signs in."""
    account = (
        await db.execute(select(LocalAccount).where(LocalAccount.kind == "bootstrap", LocalAccount.enabled.is_(True)))
    ).scalar_one_or_none()
    if account is None:
        return False
    account.enabled = False
    if account.user_id:
        await revoke_user_access(db, account.user_id, "bootstrap disabled: a GitLab admin signed in")
    return True


@dataclass
class Reevaluation:
    user_id: str
    outcome: str  # unchanged | updated | deprovisioned | skipped
    reason: str = ""


async def reevaluate(
    db: AsyncSession, client: OIDCClient, user: User, config_auth: dict[str, Any], sealer: Sealer
) -> Reevaluation:
    """Refresh the user's GitLab grant and re-read groups; revoke access when GitLab says so."""
    if user.local_kind or user.status != "active":
        return Reevaluation(user.id, "skipped", "not an active GitLab user")
    if not user.gitlab_refresh_token_enc:
        return Reevaluation(user.id, "skipped", "no refresh token")
    try:
        tokens = await client.refresh(sealer.open(user.gitlab_refresh_token_enc, REFRESH_AAD))
        info = await client.userinfo(str(tokens["access_token"]))
    except OIDCError as exc:
        if exc.reason == "provider_unreachable":
            return Reevaluation(user.id, "skipped", "GitLab unreachable")
        return await _deprovision(db, user, f"GitLab refused the grant ({exc.reason})")
    if tokens.get("refresh_token"):
        user.gitlab_refresh_token_enc = sealer.seal(str(tokens["refresh_token"]), REFRESH_AAD)
    groups = groups_from(info, str(config_auth.get("groups_claim", "groups")))
    mapping = await effective_mapping(db, config_auth)
    if not rbac.in_allowed_groups(groups, mapping.get("allowed_groups", [])):
        return await _deprovision(db, user, "no longer in an allowed group")
    roles, teams = rbac.map_groups(groups, mapping, await team_ids_by_name(db))
    if not roles:
        return await _deprovision(db, user, "no mapped group")
    user.last_reevaluated_at = now()
    if groups == list(user.groups or []):
        return Reevaluation(user.id, "unchanged")
    user.groups = groups
    await _sync_teams(db, user.id, teams)
    return Reevaluation(user.id, "updated", "groups changed")


async def _deprovision(db: AsyncSession, user: User, reason: str) -> Reevaluation:
    user.status = "deprovisioned"
    user.gitlab_refresh_token_enc = None
    user.last_reevaluated_at = now()
    await revoke_user_access(db, user.id, reason)
    return Reevaluation(user.id, "deprovisioned", reason)
