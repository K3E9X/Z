"""Scheduler loop (docs/02 §2): watchdog, reaping and dispatch on every tick; wakes early on new events."""

from __future__ import annotations

import asyncio
import logging
import socket

from z_server.executors import Executor
from z_server.scheduler import queue
from z_server.state import AppState

log = logging.getLogger("z.scheduler")


async def run_forever(state: AppState, executor: Executor, interval_s: float = 2.0) -> None:
    worker = f"{socket.gethostname()}:{id(state):x}"
    while True:
        try:
            report = await queue.tick(state, executor, worker)
            if any(report.values()):
                log.info("tick %s", {k: len(v) for k, v in report.items() if v})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await state.hub.wait(None, timeout=interval_s)
