"""Tool-call probe worker (docs/06 §5, ADR 0034): probes each internal chat alias at most every
`inference.probes.tool_call_probe_s`; the round itself checks whether it is due, across replicas."""

from __future__ import annotations

import asyncio
import logging

from z_server import probes
from z_server.state import AppState

log = logging.getLogger("z.probes")


async def run_forever(state: AppState, interval_s: float = 30.0) -> None:
    while True:
        try:
            await probes.tick(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("tool-call probe failed")
        await asyncio.sleep(interval_s)
