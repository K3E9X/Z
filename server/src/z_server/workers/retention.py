"""Data retention (docs/07 §6), swept hourly, the file and the row together:

- events of missions that ended more than `retention.events_days` ago (default 180);
- artifacts of ended missions older than `retention.artifacts_days` (default 90), C3 artifacts older than
  `retention.c3_artifacts_days` (default 7).

Missions that still run keep everything. The audit log is not touched: it is append-only for the
application's database role (invariant 9), so its retention (docs/12 §4, at least 90 days) is applied by the
database owner (ADR 0031). Deletions are idempotent, so every replica may sweep.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, or_, select
from z_common.models.enums import DataClass, MissionStatus

from z_server.db.models import Artifact, Event, Mission
from z_server.missions.service import utcnow
from z_server.state import AppState

log = logging.getLogger("z.retention")
DAY = timedelta(days=1)
BATCH = 500
ENDED = [s.value for s in MissionStatus if s.terminal]


def _days(config: dict[str, Any], key: str, default: int) -> timedelta:
    return max(1, int((config.get("retention") or {}).get(key, default))) * DAY


async def sweep(state: AppState, now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    events_before = now - _days(state.config, "events_days", 180)
    artifacts_before = now - _days(state.config, "artifacts_days", 90)
    c3_before = now - _days(state.config, "c3_artifacts_days", 7)
    out = {"events": 0, "artifacts": 0}
    async with state.sessions() as db, db.begin():
        old = select(Mission.id).where(Mission.status.in_(ENDED), Mission.ended_at < events_before)
        result = await db.execute(delete(Event).where(Event.mission_id.in_(old)))
        out["events"] = int(getattr(result, "rowcount", 0) or 0)
    while True:
        async with state.sessions() as db, db.begin():
            rows = (
                (
                    await db.execute(
                        select(Artifact)
                        .join(Mission, Mission.id == Artifact.mission_id)
                        .where(
                            Mission.status.in_(ENDED),
                            or_(
                                Artifact.created_at < artifacts_before,
                                and_(Artifact.data_class == DataClass.C3.value, Artifact.created_at < c3_before),
                            ),
                        )
                        .limit(BATCH)
                        .with_for_update(of=Artifact, skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                Path(row.storage_path).unlink(missing_ok=True)  # the content first: a row never outlives it
                await db.delete(row)
            out["artifacts"] += len(rows)
        if len(rows) < BATCH:
            break
    return out


async def run_forever(state: AppState, interval_s: float = 3600.0) -> None:
    while True:
        try:
            swept = await sweep(state)
            if any(swept.values()):
                log.info("retention removed %s", swept)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("retention sweep failed")
        await asyncio.sleep(interval_s)
