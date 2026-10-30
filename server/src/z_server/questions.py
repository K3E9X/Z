"""Answering an agent's question (docs/03 §5 "Review"): one code path for the API (`POST /questions/{id}/answer`)
and for a reply to a Z mail (docs/05 §10, ADR 0034), so the rules, the events and the audit are the same.

Rules: the person must see the mission (its starter, or `missions:read` on its team) and, unless they started
it, hold `questions:answer` on its team; a question is answered once; a question with options takes one of them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import JobStatus, MissionStatus

from z_server import audit, events
from z_server.auth import identity
from z_server.db.models import Job, Mission, Question
from z_server.errors import Problem
from z_server.missions import service
from z_server.rbac import Principal
from z_server.state import AppState

PERMISSION = "questions:answer"


def can_see(principal: Principal, mission: Mission) -> bool:
    return mission.created_by == principal.user_id or principal.can_on("missions:read", mission.team_id)


def may_answer(principal: Principal, mission: Mission) -> bool:
    return can_see(principal, mission) and (
        mission.created_by == principal.user_id or principal.can_on(PERMISSION, mission.team_id)
    )


async def answer(
    db: AsyncSession,
    state: AppState,
    principal: Principal,
    question_id: str,
    text: str,
    actor: audit.Actor,
    *,
    runs_as: str | None = None,
    note: dict[str, Any] | None = None,
) -> tuple[Question, Mission]:
    """Record `text` as the answer, emit `question_answered`, resume the job and audit it, or raise a Problem
    (not-found, forbidden, conflict, validation) before anything is written."""
    row, mission = await service.lock_with_mission(db, Question, question_id)
    if row is None or mission is None or not can_see(principal, mission):
        raise Problem("not-found")
    if mission.created_by != principal.user_id and not principal.can_on(PERMISSION, mission.team_id):
        raise Problem(
            "forbidden",
            f"missing {PERMISSION}" + (f" for team {mission.team_id}" if mission.team_id else ""),
            permission=PERMISSION,
        )
    if row.answer is not None:
        raise Problem("conflict", "this question is already answered")
    if row.options and text not in row.options:
        raise Problem("validation", f"answer must be one of: {', '.join(row.options)}")
    row.answer, row.answered_by, row.answered_at = text, principal.username, identity.now()
    job = await db.get(Job, row.job_id, with_for_update=True)
    await events.append(
        db,
        row.mission_id,
        "question_answered",
        {"question_id": row.id, "answer": text, "answered_by": principal.username},
        job_id=row.job_id,
        agent=row.agent,
    )
    if row.agent == service.JUDGE and job is not None and job.needs_attention_reason == "judge_blocked":
        if text == service.JUDGE_STOP:
            await service.finish(db, state, mission, MissionStatus.cancelled, "stopped after a judge block")
        else:  # the agent goes on without the withheld content
            job.control_state, job.control_reason = "run", None
            await service.set_job_status(db, job, JobStatus.running, "released after a judge block")
    elif job is not None and job.status == JobStatus.needs_attention.value and job.needs_attention_reason == "question":
        still_open = (
            await db.execute(
                select(Question.id).where(Question.job_id == job.id, Question.answer.is_(None), Question.id != row.id)
            )
        ).first()
        if still_open is None:
            await service.set_job_status(db, job, JobStatus.running, "question answered")
    await audit.record(
        db,
        actor,
        "question.answered",
        "question",
        row.id,
        team=mission.team_id,
        runs_as=runs_as,
        after={"mission": mission.id, "length": len(text), **(note or {})},
    )
    await service.advance(db, state, mission)
    return row, mission
