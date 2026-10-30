"""Notification delivery worker (ADR 0023): drains the outbox to the configured targets."""

from __future__ import annotations

import asyncio
import logging

from z_server import notifications
from z_server.state import AppState

log = logging.getLogger("z.notifications")


async def run_forever(state: AppState, interval_s: float = 10.0) -> None:
    while True:
        try:
            await notifications.tick(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("notification tick failed")
        await asyncio.sleep(interval_s)
