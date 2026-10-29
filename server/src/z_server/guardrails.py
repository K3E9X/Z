"""Effective platform guardrails (invariant 5): config/z.yaml, then the admin's overrides of unlocked values.

A value is either plain (`max_steps_per_job: 120`) or `{value, locked}`; a locked value ignores any override.
Everything that applies a guardrail reads it here, so an admin change reaches new jobs and the judge."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from z_server.db.models import Setting

SETTING = "guardrails"


def configured(config: dict[str, Any]) -> dict[str, tuple[Any, bool]]:
    out = {}
    for name, value in (config.get("guardrails") or {}).items():
        if isinstance(value, dict):
            out[name] = (value.get("value"), bool(value.get("locked")))
        else:
            out[name] = (value, False)
    return out


async def effective(db: AsyncSession, config: dict[str, Any]) -> dict[str, tuple[Any, bool]]:
    """name -> (value, locked)."""
    row = await db.get(Setting, SETTING)
    overrides = row.value if row else {}
    return {
        name: (overrides[name] if not locked and name in overrides else value, locked)
        for name, (value, locked) in configured(config).items()
    }


async def value(db: AsyncSession, config: dict[str, Any], name: str, default: Any = None) -> Any:
    found = (await effective(db, config)).get(name)
    return default if found is None or found[0] is None else found[0]
