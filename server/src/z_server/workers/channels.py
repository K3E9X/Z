"""Channel follow-ups worker (docs/05 §10): posts approvals, questions and outcomes to the chat thread a
mission started from (channel_threads)."""

from __future__ import annotations

import asyncio
import logging

from z_server import channel_threads
from z_server.state import AppState

log = logging.getLogger("z.channels")


async def run_forever(state: AppState, interval_s: float = 5.0) -> None:
    while True:
        try:
            await channel_threads.tick(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("channel follow-up tick failed")
        await asyncio.sleep(interval_s)
