"""Hourly GitLab group re-evaluation of active users (docs/12 §1 "Lifecycle")."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import or_, select

from z_server import audit
from z_server.auth import gitlab, identity
from z_server.db.models import AuthSession, User
from z_server.state import AppState

log = logging.getLogger("z.identity")


async def reevaluate_due(state: AppState, older_than_s: int | None = None) -> list[gitlab.Reevaluation]:
    """Re-evaluate GitLab users with a live session whose last evaluation is older than the period."""
    async with state.sessions() as db, db.begin():
        auth = await state.auth_settings(db)
        period = older_than_s if older_than_s is not None else int(auth.get("reevaluate_groups_minutes", 60)) * 60
        cutoff = identity.now() - timedelta(seconds=period)
        live = select(AuthSession.user_id).where(
            AuthSession.revoked_at.is_(None), AuthSession.expires_at > identity.now()
        )
        users = (
            (
                await db.execute(
                    select(User).where(
                        User.id.in_(live),
                        User.status == "active",
                        User.local_kind.is_(None),
                        or_(User.last_reevaluated_at.is_(None), User.last_reevaluated_at <= cutoff),
                    )
                )
            )
            .scalars()
            .all()
        )
        ids = [u.id for u in users]
    results = []
    for user_id in ids:
        async with state.sessions() as db, db.begin():  # one transaction per user
            user = await db.get(User, user_id, with_for_update=True)
            if user is None:
                continue
            client = await state.oidc(db)
            result = await gitlab.reevaluate(db, client, user, await state.auth_settings(db), state.sealer)
            if result.outcome in {"deprovisioned", "updated"}:
                await audit.record(
                    db,
                    audit.Actor("system"),
                    f"auth.reevaluation_{result.outcome}",
                    "user",
                    user.id,
                    after={"reason": result.reason, "groups": user.groups},
                )
            results.append(result)
    return results


async def run_forever(state: AppState, interval_s: int = 300) -> None:
    while True:
        try:
            await reevaluate_due(state)
        except Exception:
            log.exception("group re-evaluation failed")
        await asyncio.sleep(interval_s)
