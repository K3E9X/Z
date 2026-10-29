"""Action executor (docs/05 §2, invariant 4): applies approved actions with the connector's write credential.

- Claim: `approved` actions, `FOR UPDATE SKIP LOCKED`, moved to `applying` in the same transaction.
- Defence in depth, checked again right before the write: enough distinct approvers, the operation still
  exists, is still exposed and still a write of an approved, enabled connector; the credential scope is
  available (user-scope delegated grants arrive with personal connections).
- Idempotency: adapters first look for an earlier application (Jira: the z-<action> label or the `z` comment
  property). An action found `applying` after a crash is recovered: found -> applied, idempotent operation ->
  applied again, otherwise -> failed with "outcome unknown" so a human checks the tool (never a blind retry).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from z_server import audit, events
from z_server.db.models import ActionApproval, Connector, Mission, Operation, ProposedAction
from z_server.gateway.core import Gateway
from z_server.gateway.writers import Applied, WriteError, idempotent, writer_for
from z_server.missions import service
from z_server.missions.service import utcnow
from z_server.state import AppState

log = logging.getLogger("z.executor")
STALE_APPLYING = timedelta(minutes=5)
MAX_PARALLEL = 4


async def _transition(
    db: Any, action: ProposedAction, to: str, result: dict[str, Any] | None = None, by: str = "executor"
) -> None:
    previous = action.status
    action.status = to
    if result is not None:
        action.result = result
    await events.append(
        db,
        action.mission_id,
        "action_status",
        {"action_id": action.id, "from": previous, "to": to, "by": by, "result": result},
        job_id=action.job_id,
        agent=action.agent,
    )


async def claim(state: AppState, limit: int = MAX_PARALLEL) -> list[str]:
    async with state.sessions() as db, db.begin():
        rows = (
            (
                await db.execute(
                    select(ProposedAction)
                    .where(ProposedAction.status == "approved")
                    .order_by(ProposedAction.updated_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for action in rows:
            action.apply_attempts += 1
            await _transition(db, action, "applying")
        return [a.id for a in rows]


async def _preconditions(db: Any, action: ProposedAction) -> tuple[Connector, Operation]:
    approvers = (
        await db.execute(
            select(func.count(func.distinct(ActionApproval.user_id))).where(ActionApproval.action_id == action.id)
        )
    ).scalar_one()
    if approvers < action.required_approvals:
        raise WriteError(f"{approvers} distinct approvals, {action.required_approvals} required")
    op = await db.get(Operation, action.operation)
    connector = await db.get(Connector, op.connector_id) if op else None
    if op is None or connector is None:
        raise WriteError("the operation no longer exists")
    if not (op.exposed and connector.enabled and connector.review_status == "approved") or op.op_class == "read":
        raise WriteError("the operation is no longer exposed for writes")
    if (action.runs_as or {}).get("scope") == "user":
        raise WriteError("user-scope credentials need a personal connection, not available on this server yet")
    return connector, op


async def _finish(state: AppState, action_id: str, status: str, result: dict[str, Any], runs_as: str | None) -> None:
    async with state.sessions() as db, db.begin():
        action = await db.get(ProposedAction, action_id, with_for_update=True)
        if action is None or action.status != "applying":
            return
        await _transition(db, action, status, result)
        mission = await db.get(Mission, action.mission_id)
        await audit.record(
            db,
            audit.Actor("system:executor"),
            f"action.{status}",
            "action",
            action.id,
            result="success" if status == "applied" else "error",
            team=mission.team_id if mission else None,
            runs_as=runs_as or (action.runs_as or {}).get("identity"),
            after={
                "operation": action.operation,
                "mission": action.mission_id,
                **{k: v for k, v in result.items() if k != "error_detail"},
            },
        )
        if mission is not None:
            await service.advance(db, state, mission)


async def apply(state: AppState, gateway: Gateway, action_id: str, *, recovering: bool = False) -> str:
    async with state.sessions() as db:
        action = await db.get(ProposedAction, action_id)
        if action is None:
            return "missing"
        try:
            connector, op = await _preconditions(db, action)
        except WriteError as exc:
            await _finish(state, action_id, "failed", {"error": str(exc)}, None)
            return "failed"
        db.expunge_all()
    writer = writer_for(connector, op)
    try:
        found = await writer.lookup(gateway, connector, op, action)
        if found is not None:
            await _finish(state, action_id, "applied", found, None)
            return "applied"
        if recovering and not idempotent(op):
            await _finish(
                state,
                action_id,
                "failed",
                {"error": "outcome unknown after an interruption; check the tool before re-proposing"},
                None,
            )
            return "failed"
        applied: Applied = await writer.apply(gateway, connector, op, action)
    except WriteError as exc:
        await _finish(state, action_id, "failed", {"error": str(exc)[:1000]}, None)
        return "failed"
    except Exception as exc:
        log.exception("executor crashed on %s", action_id)
        await _finish(state, action_id, "failed", {"error": f"executor error: {type(exc).__name__}"}, None)
        return "failed"
    await _finish(state, action_id, "applied", applied.result, applied.invocation.identity)
    return "applied"


async def recover(state: AppState, gateway: Gateway) -> list[str]:
    cutoff = utcnow() - STALE_APPLYING
    async with state.sessions() as db:
        stale = (
            (
                await db.execute(
                    select(ProposedAction.id).where(
                        ProposedAction.status == "applying", ProposedAction.updated_at < cutoff
                    )
                )
            )
            .scalars()
            .all()
        )
    for action_id in stale:
        await apply(state, gateway, action_id, recovering=True)
    return list(stale)


async def tick(state: AppState, gateway: Gateway) -> dict[str, list[str]]:
    recovered = await recover(state, gateway)
    claimed = await claim(state)
    outcomes = await asyncio.gather(*(apply(state, gateway, a) for a in claimed))
    return {
        "recovered": recovered,
        "applied": [a for a, o in zip(claimed, outcomes, strict=True) if o == "applied"],
        "failed": [a for a, o in zip(claimed, outcomes, strict=True) if o == "failed"],
    }


async def run_forever(state: AppState, interval_s: float = 2.0) -> None:
    while True:
        try:
            if state.gateway is not None:
                report = await tick(state, state.gateway)
                if any(report.values()):
                    log.info("executor %s", {k: len(v) for k, v in report.items() if v})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("executor tick failed")
        await state.hub.wait(None, timeout=interval_s)
