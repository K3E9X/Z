"""Connector health monitor (docs/05 §5): probe every approved, enabled connector on its interval and merge the
gateway's call statistics (p50/p95, error rate). Credentials expiring within 7 days mark `token_expiring`."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from z_server import notifications
from z_server.auth.identity import now
from z_server.db.models import Connector, Credential
from z_server.gateway import admin
from z_server.state import AppState

log = logging.getLogger("z.health")
EXPIRY_WARNING = timedelta(days=7)
DEGRADED_ERROR_RATE = 0.2


def _due(health: dict[str, object], interval_s: int, at: datetime) -> bool:
    checked = health.get("checked_at")
    if not isinstance(checked, str):
        return True
    try:
        return datetime.fromisoformat(checked) + timedelta(seconds=interval_s) <= at
    except ValueError:
        return True


async def check_all(state: AppState) -> dict[str, str]:
    at = now()
    async with state.sessions() as db:
        connectors = list(
            (
                await db.execute(
                    select(Connector).where(Connector.enabled.is_(True), Connector.review_status == "approved")
                )
            ).scalars()
        )
        expiring = {
            c.connector_id
            for c in (
                await db.execute(
                    select(Credential).where(
                        Credential.expires_at.is_not(None), Credential.expires_at < at + EXPIRY_WARNING
                    )
                )
            ).scalars()
        }
        db.expunge_all()
    results: dict[str, str] = {}
    for connector in connectors:
        interval = int((connector.manifest.get("health") or {}).get("interval_s", 60))
        if not _due(connector.health or {}, interval, at):
            continue
        try:
            probe = await admin.probe(state, connector)
        except Exception as exc:  # noqa: BLE001 - one broken connector must not stop the monitor
            probe = {"ok": False, "latency_ms": 0, "detail": f"probe error: {type(exc).__name__}"}
        stats = state.gateway.stats.summary(connector.name) if state.gateway else {}
        if not probe["ok"] and "no health probe" in str(probe["detail"]):
            status = (
                "degraded"
                if (stats.get("error_rate") or 0) > DEGRADED_ERROR_RATE
                else "healthy"
                if stats.get("calls")
                else "unreachable"
            )
        else:
            status = (
                "unreachable"
                if not probe["ok"]
                else ("degraded" if (stats.get("error_rate") or 0) > DEGRADED_ERROR_RATE else "healthy")
            )
        if status == "healthy" and connector.id in expiring:
            status = "token_expiring"
        health = {
            "status": status,
            "latency_p50_ms": stats.get("latency_p50_ms"),
            "latency_p95_ms": stats.get("latency_p95_ms") or (probe["latency_ms"] or None),
            "error_rate": stats.get("error_rate"),
            "last_success_at": at.isoformat() if probe["ok"] else (connector.health or {}).get("last_success_at"),
            "checked_at": at.isoformat(),
            "detail": str(probe["detail"])[:300],
        }
        async with state.sessions() as db, db.begin():
            row = await db.get(Connector, connector.id)
            if row is not None:
                was = (row.health or {}).get("status")
                row.health = health
                if status == "unreachable" and was not in {None, "unreachable"}:
                    notifications.enqueue(
                        db,
                        "connector_unhealthy",
                        payload={"connector": connector.name, "status": status, "detail": health["detail"]},
                    )
        results[connector.name] = status
    return results


async def run_forever(state: AppState, interval_s: float = 30.0) -> None:
    while True:
        try:
            await check_all(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("health check failed")
        await asyncio.sleep(interval_s)
