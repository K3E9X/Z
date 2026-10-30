"""E-mail digest worker (docs/05 §10, ADR 0034): enqueues the daily decisions and weekly report mails when due."""

from __future__ import annotations

import asyncio
import logging

from z_server import digests
from z_server.state import AppState

log = logging.getLogger("z.digests")


async def run_forever(state: AppState, interval_s: float = 60.0) -> None:
    while True:
        try:
            await digests.tick(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("digest tick failed")
        await asyncio.sleep(interval_s)
