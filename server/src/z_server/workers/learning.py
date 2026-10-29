"""Learning worker (docs/08): grades finished eval runs, closes proposal evaluations, and drafts proposals on
the `learning.schedule` cron (default 02:00 UTC every night)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from z_server import evals, learner
from z_server.intake.cron import Cron, CronError
from z_server.state import AppState

log = logging.getLogger("z.learning")


async def tick(state: AppState, at: datetime | None = None) -> dict[str, list[str]]:
    moment = at or datetime.now(UTC)
    graded = await evals.tick(state)
    decided = await learner.gate(state)
    drafted: list[str] = []
    due = state.cache.get("learner_next")
    if due is None:
        state.cache["learner_next"] = _next(state, moment)
    elif due <= moment:
        state.cache["learner_next"] = _next(state, moment)
        drafted = await learner.observe(state)
    return {"graded": graded, "decided": decided, "drafted": drafted}


def _next(state: AppState, after: datetime) -> datetime:
    try:
        return Cron(str((state.config.get("learning") or {}).get("schedule", "0 2 * * *")), "UTC").next_after(after)
    except CronError as exc:
        log.warning("learning.schedule: %s; the learner runs nightly at 02:00 UTC", exc)
        return Cron("0 2 * * *", "UTC").next_after(after)


async def run_forever(state: AppState, interval_s: float = 30.0) -> None:
    while True:
        try:
            await tick(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("learning tick failed")
        await asyncio.sleep(interval_s)
