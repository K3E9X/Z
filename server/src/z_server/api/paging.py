"""Cursor pagination: ids are time-ordered, so the cursor is the last id returned (newest first)."""

from __future__ import annotations

from typing import Any, TypeVar

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

T = TypeVar("T")
MAX_LIMIT = 200


async def page(
    db: AsyncSession, stmt: Select[Any], key: InstrumentedAttribute[Any], cursor: str | None, limit: int
) -> tuple[list[Any], str | None]:
    limit = max(1, min(limit, MAX_LIMIT))
    if cursor:
        stmt = stmt.where(key < cursor)
    rows = list((await db.execute(stmt.order_by(key.desc()).limit(limit + 1))).scalars())
    more = len(rows) > limit
    rows = rows[:limit]
    return rows, (str(getattr(rows[-1], key.key)) if more and rows else None)
