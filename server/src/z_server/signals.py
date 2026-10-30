"""Learning signals (docs/08 §2): cheap rows the learner clusters by agent, skill and pattern.

Most signals are derived from mission events as they are appended (denied calls and judge flags, repaired
output, failed jobs, jobs a person retried, missions cancelled with a written reason, answered questions,
actions a person rejected, failed tool calls); edits of proposed actions are
recorded by the review route with the before/after payloads. Eval missions emit none: they would teach the
learner about its own tests.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import SignalKind

from z_server.db.models import Mission, Signal
from z_server.ids import new_id

MAX_TEXT = 2000


def kind_for(event_type: str, payload: dict[str, Any]) -> SignalKind | None:
    if event_type == "denied":
        return SignalKind.guardrail_hit
    if event_type == "judge" and payload.get("verdict") != "clean":
        return SignalKind.guardrail_hit
    if event_type == "validation_error":
        return SignalKind.repair
    if event_type == "status" and payload.get("entity") == "job" and payload.get("to") == "failed":
        return SignalKind.failure
    if (
        event_type == "status"
        and payload.get("entity") == "job"
        and payload.get("to") == "queued"
        and str(payload.get("reason") or "").startswith("retry of a ")  # a person retried it (service.retry_job)
    ):
        return SignalKind.retry
    if (
        event_type == "status"
        and payload.get("entity") == "mission"
        and payload.get("to") == "cancelled"
        and payload.get("reason")
        and not str(payload["reason"]).startswith(("cancelled by ", "stopped after a judge block"))
    ):
        return SignalKind.feedback  # cancelled with a reason the person wrote (docs/04 §7)
    if event_type == "question_answered" and payload.get("answered_by") != "z:eval":
        return SignalKind.question_answered
    if (
        event_type == "action_status"
        and payload.get("to") == "rejected"
        and payload.get("by") != "system"  # closed because the mission ended, not a person's judgement
        and not (payload.get("result") or {}).get("captured")
    ):
        return SignalKind.action_rejected
    if event_type == "tool_result" and payload.get("ok") is False:
        return SignalKind.connector_error
    return None


def _trim(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_trim(v) for v in value[:50]]
    return value


async def emit(
    db: AsyncSession,
    kind: SignalKind,
    payload: dict[str, Any],
    *,
    mission_id: str | None = None,
    job_id: str | None = None,
    agent: str | None = None,
    skill: str | None = None,
) -> Signal | None:
    if mission_id:
        mission = await db.get(Mission, mission_id)
        if mission is not None and mission.eval_run_id:
            return None
    row = Signal(
        id=new_id("sig"),
        kind=kind.value,
        mission_id=mission_id,
        job_id=job_id,
        agent=agent,
        skill=skill,
        payload=_trim(payload),
    )
    db.add(row)
    return row


async def from_event(
    db: AsyncSession, mission_id: str, event_type: str, payload: dict[str, Any], job_id: str | None, agent: str | None
) -> None:
    kind = kind_for(event_type, payload)
    if kind is not None:
        await emit(db, kind, {"event": event_type, **payload}, mission_id=mission_id, job_id=job_id, agent=agent)
