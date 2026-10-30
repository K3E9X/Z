"""auth.mode dev: a single local user, for development only (refused when Z_ENV=production)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from z_server.auth.identity import build_principal
from z_server.db.models import User
from z_server.ids import new_id
from z_server.rbac import Principal
from z_server.state import AppState

DEV_ISSUER = "dev"


async def dev_user(db: AsyncSession, state: AppState) -> User:
    username = str(state.config_auth.get("dev_user", {}).get("username", "dev"))
    user = (await db.execute(select(User).where(User.issuer == DEV_ISSUER, User.sub == username))).scalar_one_or_none()
    if user is None:
        user = User(
            id=new_id("usr"),
            issuer=DEV_ISSUER,
            sub=username,
            username=username,
            name=username,
            status="active",
            local_kind="dev",
        )
        db.add(user)
        await db.flush()
    return user


async def dev_principal(db: AsyncSession, state: AppState) -> Principal:
    if state.settings.production:
        raise RuntimeError("dev auth in production")
    return await build_principal(db, await dev_user(db, state), state.config_auth, kind="dev")
