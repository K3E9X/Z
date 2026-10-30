"""Mission events: append with a per-mission sequence, server-side redaction, wake-ups for live streams.

seq comes from `missions.next_seq` (row lock), so events of one mission are totally ordered and gap-free.
A `pg_notify('z_events', '<mission_id>:<seq>')` wakes SSE streams; streams also poll, so a lost
notification only delays delivery.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import psycopg
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.events import PAYLOADS
from z_common.redaction import Redactor

from z_server import notifications, signals
from z_server.db.models import Event, Mission

log = logging.getLogger("z.events")
CHANNEL = "z_events"


class EventValidationError(ValueError):
    pass


async def append(
    db: AsyncSession,
    mission_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    job_id: str | None = None,
    agent: str | None = None,
    step_id: str | None = None,
    ts: datetime | None = None,
    secrets: Iterable[str] = (),
) -> Event:
    if event_type not in PAYLOADS:
        raise EventValidationError(f"unknown event type {event_type!r}")
    clean = Redactor(secrets).value(payload)
    try:
        model = PAYLOADS[event_type].model_validate(clean)
    except ValueError as exc:
        raise EventValidationError(f"{event_type}: {exc}") from exc
    result = await db.execute(
        update(Mission)
        .where(Mission.id == mission_id)
        .values(next_seq=Mission.next_seq + 1)
        .returning(Mission.next_seq)
    )
    seq = int(result.scalar_one()) - 1
    row = Event(
        mission_id=mission_id,
        seq=seq,
        job_id=job_id,
        ts=ts or datetime.now(UTC),
        type=event_type,
        agent=agent,
        step_id=step_id,
        payload=model.model_dump(mode="json", by_alias=True),
    )
    db.add(row)
    await signals.from_event(db, mission_id, event_type, row.payload, job_id, agent)
    notify = notifications.kind_for_event(event_type, row.payload)
    if notify is not None:
        mission = await db.get(Mission, mission_id)
        notifications.enqueue(db, notify, mission=mission, event_seq=seq)
    await db.flush()
    await db.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": f"{mission_id}:{seq}"})
    return row


async def since(
    db: AsyncSession, mission_id: str, after_seq: int, *, job_id: str | None = None, limit: int = 500
) -> list[Event]:
    stmt = select(Event).where(Event.mission_id == mission_id, Event.seq > after_seq).order_by(Event.seq).limit(limit)
    if job_id:
        stmt = stmt.where(Event.job_id == job_id)
    return list((await db.execute(stmt)).scalars())


def as_contract(row: Event) -> dict[str, Any]:
    return {
        "mission_id": row.mission_id,
        "job_id": row.job_id,
        "seq": row.seq,
        "ts": row.ts.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "type": row.type,
        "agent": row.agent,
        "step_id": row.step_id,
        "payload": row.payload,
    }


class EventHub:
    """In-process wake-ups from Postgres LISTEN; one listener task per server process."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
        self._conditions: dict[str, asyncio.Condition] = defaultdict(asyncio.Condition)
        self._global = asyncio.Condition()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def _listen(self) -> None:
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as conn:
                    await conn.execute(f"LISTEN {CHANNEL}")
                    async for note in conn.notifies():
                        await self._wake(note.payload.split(":", 1)[0])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("event listener disconnected, retrying", exc_info=True)
                await asyncio.sleep(2)

    async def _wake(self, mission_id: str) -> None:
        for condition in (self._conditions[mission_id], self._global):
            async with condition:
                condition.notify_all()

    async def wait(self, mission_id: str | None, timeout: float) -> None:
        condition = self._conditions[mission_id] if mission_id else self._global
        async with condition:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(condition.wait(), timeout)
