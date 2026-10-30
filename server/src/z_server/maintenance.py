"""Maintenance mode (getMaintenance / setMaintenance): while on, no mission or job starts and running jobs finish.

The switch lives in the `settings` table (key `maintenance`: enabled, message, since, by). Starts are refused at
the one place every start goes through (`missions.service.start`, `retry_job`), the scheduler claims nothing,
the intake loop fires no schedule and polls no tracker, and every signed-in user sees the message as a banner.
Reads on the request path go to the database (they are a primary-key lookup in the transaction that starts);
the banner and the intake loop use a short per-process cache, cleared on change.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from z_server.db.models import Setting
from z_server.errors import Problem
from z_server.state import AppState

KEY = "maintenance"
CACHE_KEY = "maintenance"
CACHE_S = 5.0
DEFAULT_MESSAGE = "Z is in maintenance: no mission or job starts until it ends. Running jobs finish."
OFF: dict[str, Any] = {"enabled": False, "message": None, "since": None, "by": None}


def _clean(value: dict[str, Any] | None) -> dict[str, Any]:
    out = {**OFF, **{k: v for k, v in (value or {}).items() if k in OFF}}
    out["enabled"] = bool(out["enabled"])
    return out


async def read(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(Setting, KEY)
    return _clean(row.value if row else None)


async def write(db: AsyncSession, enabled: bool, message: str | None, by: str) -> dict[str, Any]:
    before = await read(db)
    if enabled:
        since = before["since"] if before["enabled"] and before["since"] else datetime.now(UTC).isoformat()
        value = {"enabled": True, "message": message or None, "since": since, "by": by}
    else:
        value = dict(OFF)
    row = await db.get(Setting, KEY, with_for_update=True)
    if row is None:
        db.add(Setting(key=KEY, value=value, updated_by=by))
    else:
        row.value, row.updated_by = value, by
    await db.flush()
    return value


def text(value: dict[str, Any]) -> str:
    return str(value.get("message") or DEFAULT_MESSAGE)


async def refuse_starts(db: AsyncSession) -> None:
    """Problem `unavailable` while maintenance is on: the caller's start is refused, nothing is half-started."""
    value = await read(db)
    if value["enabled"]:
        raise Problem("unavailable", f"maintenance mode: nothing starts until it ends. {text(value)}")


async def current(state: AppState) -> dict[str, Any]:
    """The setting, cached for CACHE_S in this process (banners, background loops)."""
    hit = state.cache.get(CACHE_KEY)
    if hit and hit[0] > time.monotonic():
        return dict(hit[1])
    async with state.sessions() as db:
        value = await read(db)
    state.cache[CACHE_KEY] = (time.monotonic() + CACHE_S, value)
    return dict(value)


def forget(state: AppState) -> None:
    state.cache.pop(CACHE_KEY, None)
