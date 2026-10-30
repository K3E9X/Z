"""Database rows -> contract models."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models import api_identity as m

from z_server.auth.identity import roles_for_user
from z_server.db import models as db
from z_server.state import AppState


async def teams_of(session: AsyncSession, user_id: str) -> list[m.TeamRef]:
    rows = (
        await session.execute(
            select(db.Team).join(db.UserTeam, db.UserTeam.team_id == db.Team.id).where(db.UserTeam.user_id == user_id)
        )
    ).scalars()
    return [m.TeamRef(id=t.id, name=t.name) for t in rows]


async def user_out(session: AsyncSession, state: AppState, user: db.User) -> m.User:
    roles, _ = await roles_for_user(session, user, state.config_auth)
    return m.User(
        id=user.id,
        issuer=user.issuer,
        sub=user.sub,
        username=user.username,
        name=user.name,
        email=user.email,
        avatar_url=user.avatar_url,
        teams=await teams_of(session, user.id),
        status=user.status,
        last_login_at=user.last_login_at,
        mfa_enforced=user.mfa_enforced,
        local_kind=user.local_kind,
        effective_roles=[
            m.EffectiveRole(role=r.role, scope=r.scope, source=r.source, via=r.via, expires_at=r.expires_at)
            for r in roles
        ],
        created_at=user.created_at,
    )


def session_out(row: db.AuthSession, username: str, current_id: str | None = None) -> m.AuthSession:
    return m.AuthSession(
        id=row.id,
        user=username,
        kind=row.kind,
        device=row.device,
        ip=row.ip,
        user_agent=row.user_agent,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        expires_at=row.expires_at,
        step_up_at=row.step_up_at,
        revoked_at=row.revoked_at,
        current=row.id == current_id if current_id else None,
    )


def token_out(row: db.ApiToken, username: str, token: str | None = None) -> m.ApiToken:
    return m.ApiToken(
        id=row.id,
        user=username,
        name=row.name,
        scopes=list(row.scopes),
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
        token=token,
    )


def audit_out(row: db.AuditEvent) -> m.AuditEvent:
    return m.AuditEvent(
        seq=row.seq,
        ts=row.ts,
        actor=row.actor,
        actor_ip=row.actor_ip,
        user_agent=row.user_agent,
        session_id=row.session_id,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        team=row.team,
        result=row.result,
        runs_as=row.runs_as,
        before=row.before,
        after=row.after,
        request_id=row.request_id,
        prev_hash=row.prev_hash,
        hash=row.hash,
    )
