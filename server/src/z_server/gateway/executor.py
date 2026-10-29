"""Action executor (docs/05 §2, invariant 4): applies approved actions with the connector's write credential.

- Claim: `approved` actions, `FOR UPDATE SKIP LOCKED`, moved to `applying` in the same transaction.
- Defence in depth, checked again right before the write: enough distinct approvers, the operation still
  exists, is still exposed and still a write of an approved, enabled connector; the credential scope is
  available; user-scope actions use the write grant of the person they run as (personal connections).
- Idempotency: adapters first look for an earlier application (Jira: the z-<action> label or the `z` comment
  property). An action found `applying` after a crash is recovered: found -> applied, idempotent operation ->
  applied again, otherwise -> failed with "outcome unknown" so a human checks the tool (never a blind retry).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from z_server import audit, events
from z_server.db.models import ActionApproval, Artifact, Connector, Mission, Operation, ProposedAction
from z_server.gateway import oauth
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
        # the action and its mission are locked together and never waited for (SKIP LOCKED on both rows), so
        # this never deadlocks with a transaction that holds the mission (approve, cancel) and wants the action
        rows = (
            (
                await db.execute(
                    select(ProposedAction)
                    .join(Mission, Mission.id == ProposedAction.mission_id)
                    .where(ProposedAction.status == "approved")
                    .order_by(ProposedAction.updated_at)
                    .limit(limit)
                    .with_for_update(of=[ProposedAction, Mission], skip_locked=True)
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
    if (action.runs_as or {}).get("scope") == "user" and not (action.runs_as or {}).get("user_id"):
        raise WriteError("a user-scope action has no user to act as")
    return connector, op


async def _artifacts(db: Any, action: ProposedAction) -> dict[str, bytes]:
    """Bytes of the artifacts the payload references; only artifacts of the action's own mission."""
    out: dict[str, bytes] = {}
    for key, value in action.payload.items():
        if not key.endswith("_artifact") or not isinstance(value, str):
            continue
        row = await db.get(Artifact, value)
        if row is None or row.mission_id != action.mission_id:
            raise WriteError(f"{key} is not an artifact of this mission")
        path = Path(row.storage_path)
        if not path.is_file():
            raise WriteError(f"{key} content is missing from storage")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row.sha256:
            raise WriteError(f"{key} changed after it was reviewed")
        out[key] = data
    return out


async def _finish(state: AppState, action_id: str, status: str, result: dict[str, Any], runs_as: str | None) -> None:
    async with state.sessions() as db, db.begin():
        action, mission = await service.lock_with_mission(db, ProposedAction, action_id)
        if action is None or action.status != "applying":
            return
        await _transition(db, action, status, result)
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
        try:
            files = await _artifacts(db, action)
        except WriteError as exc:
            await _finish(state, action_id, "failed", {"error": str(exc)}, None)
            return "failed"
        db.expunge_all()
    writer = writer_for(connector, op)
    runs_as = action.runs_as or {}
    # user-scope actions use the write grant of the person they run as (the mission starter), nobody else's
    acting = oauth.ACTING_USER.set(str(runs_as["user_id"]) if runs_as.get("scope") == "user" else None)
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
        applied: Applied = await writer.apply(gateway, connector, op, action, files)
    except WriteError as exc:
        await _finish(state, action_id, "failed", {"error": str(exc)[:1000]}, None)
        return "failed"
    except Exception as exc:
        log.exception("executor crashed on %s", action_id)
        await _finish(state, action_id, "failed", {"error": f"executor error: {type(exc).__name__}"}, None)
        return "failed"
    finally:
        oauth.ACTING_USER.reset(acting)
    await _finish(state, action_id, "applied", applied.result, applied.invocation.identity)
    return "applied"


async def recover(state: AppState, gateway: Gateway) -> list[str]:
    cutoff = utcnow() - STALE_APPLYING
    # claimed like `claim` (one replica recovers an action; the others skip it) and stamped, so a second
    # replica does not take it again while this one is still looking it up in the tool
    async with state.sessions() as db, db.begin():
        rows = (
            (
                await db.execute(
                    select(ProposedAction)
                    .join(Mission, Mission.id == ProposedAction.mission_id)
                    .where(ProposedAction.status == "applying", ProposedAction.updated_at < cutoff)
                    .limit(MAX_PARALLEL)
                    .with_for_update(of=[ProposedAction, Mission], skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.updated_at = utcnow()
        stale = [row.id for row in rows]
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
