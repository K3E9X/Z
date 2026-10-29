"""Review inbox: proposed actions, agent questions, mission suggestions (docs/03 §5 "Review", docs/12 §3).

Invariant 4: nothing is written without a human. Rules applied here:
- approver scope: `actions:approve` on the mission's team (or org-wide);
- separation of duties: with four_eyes the mission starter cannot approve its writes;
- destructive actions need two distinct approvers, each with a fresh step-up; test accounts never give the second;
- an edit supersedes the action and re-proposes it; approvals never carry over.
Approved actions go to the action executor (B3), which applies them idempotently.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import jsonschema
from sqlalchemy import or_, select
from z_common.models import api_missions as m
from z_common.models.actions import BUILTIN_PAYLOADS
from z_common.models.enums import JobStatus, OperationClass

from z_server import events
from z_server.actions.previews import build as build_preview
from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.api.routes_internal import action_out, question_out
from z_server.api.routes_missions import can_see
from z_server.auth import identity
from z_server.db.models import (
    ActionApproval,
    Agent,
    Job,
    Mission,
    MissionSuggestion,
    Operation,
    ProposedAction,
    Question,
)
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.missions import service

router = ContractRouter()


async def _action(ctx: Ctx, action_id: str, *, lock: bool = False) -> tuple[ProposedAction, Mission]:
    row = await ctx.db.get(ProposedAction, action_id, with_for_update=lock)
    mission = await ctx.db.get(Mission, row.mission_id) if row else None
    if row is None or mission is None or not can_see(ctx, mission):
        raise Problem("not-found")
    return row, mission


def _may_decide(ctx: Ctx, mission: Mission) -> None:
    ctx.require("actions:approve", mission.team_id)


async def _set_status(ctx: Ctx, row: ProposedAction, to: str, result: dict[str, Any] | None = None) -> None:
    previous = row.status
    row.status = to
    await events.append(
        ctx.db,
        row.mission_id,
        "action_status",
        {"action_id": row.id, "from": previous, "to": to, "by": ctx.user.username, "result": result},
        job_id=row.job_id,
        agent=row.agent,
    )


async def _advance(ctx: Ctx, mission_id: str) -> None:
    mission = await ctx.db.get(Mission, mission_id)
    if mission is not None:
        await service.advance(ctx.db, ctx.state, mission)


async def approve(ctx: Ctx, row: ProposedAction, mission: Mission, body: m.ApproveRequest) -> None:
    _may_decide(ctx, mission)
    if row.status != "pending":
        raise Problem("conflict", f"action is {row.status}")
    user = ctx.user
    if row.four_eyes and mission.created_by == user.user_id:
        raise Problem("separation-of-duties", "four-eyes: the mission starter cannot approve its writes")
    previous = list((await ctx.db.execute(select(ActionApproval).where(ActionApproval.action_id == row.id))).scalars())
    if any(a.user_id == user.user_id for a in previous):
        raise Problem(
            "separation-of-duties", "you already approved this action; another approver must give the next approval"
        )
    destructive = row.op_class == OperationClass.destructive.value
    fresh = (
        user.step_up_at is not None
        and user.step_up_at + timedelta(seconds=await ctx.state.step_up_max_age(ctx.db)) >= identity.now()
    )
    if destructive:
        if not fresh and user.kind != "dev":
            raise Problem(
                "step-up-required",
                "re-authenticate to approve a destructive action",
                step_up_url="/api/v1/auth/step-up",
            )
        if previous and user.local_kind == "test":
            raise Problem("separation-of-duties", "test accounts never give the second approval on destructive actions")
    if body.exclude:
        items = row.payload.get("items")
        if not isinstance(items, list):
            raise Problem("validation", "this action has no items to exclude")
        row.payload = {
            **row.payload,
            "items": [i for i in items if not (isinstance(i, dict) and str(i.get("id")) in set(body.exclude))],
        }
    ctx.db.add(
        ActionApproval(id=new_id("apr"), action_id=row.id, user_id=user.user_id, step_up=fresh, comment=body.comment)
    )
    await ctx.db.flush()
    await ctx.audit(
        "action.approval_given",
        "action",
        row.id,
        team=mission.team_id,
        runs_as=row.runs_as.get("identity"),
        after={
            "operation": row.operation,
            "class": row.op_class,
            "approvals": len(previous) + 1,
            "required": row.required_approvals,
            "excluded": body.exclude or None,
        },
    )
    if len(previous) + 1 >= row.required_approvals:
        await _set_status(ctx, row, "approved")
        await ctx.audit(
            "action.approved",
            "action",
            row.id,
            team=mission.team_id,
            runs_as=row.runs_as.get("identity"),
            after={"operation": row.operation},
        )


@router.operation("getAction")
async def get_action(ctx: CtxDep, id: str) -> m.ProposedAction:
    row, _ = await _action(ctx, id)
    return await action_out(ctx.db, row)


@router.operation("approveAction")
async def approve_action(ctx: CtxDep, id: str, body: m.ApproveRequest) -> m.ProposedAction:
    row, mission = await _action(ctx, id, lock=True)
    await approve(ctx, row, mission, body)
    await _advance(ctx, mission.id)
    return await action_out(ctx.db, row)


@router.operation("rejectAction")
async def reject_action(ctx: CtxDep, id: str, body: m.RejectRequest) -> m.ProposedAction:
    row, mission = await _action(ctx, id, lock=True)
    _may_decide(ctx, mission)
    if row.status != "pending":
        raise Problem("conflict", f"action is {row.status}")
    await _set_status(ctx, row, "rejected", {"reason": body.reason[:2000]})
    row.result = {"rejected_by": ctx.user.username, "reason": body.reason[:2000]}
    await ctx.audit(
        "action.rejected",
        "action",
        row.id,
        team=mission.team_id,
        after={"operation": row.operation, "reason": body.reason[:500]},
    )
    await _advance(ctx, mission.id)
    return await action_out(ctx.db, row)


@router.operation("editAction")
async def edit_action(ctx: CtxDep, id: str, body: m.EditRequest) -> m.ProposedAction:
    row, mission = await _action(ctx, id, lock=True)
    _may_decide(ctx, mission)
    if row.status != "pending":
        raise Problem("conflict", f"action is {row.status}")
    op = await ctx.db.get(Operation, row.operation)
    if op is None:
        raise Problem("conflict", "the operation of this action no longer exists")
    try:
        jsonschema.validate(body.payload, op.input_schema or {"type": "object"})
        if row.operation in BUILTIN_PAYLOADS:
            BUILTIN_PAYLOADS[row.operation].model_validate(body.payload)
    except (jsonschema.ValidationError, ValueError) as exc:
        raise Problem(
            "validation", f"payload does not match {row.operation}: {getattr(exc, 'message', str(exc))[:500]}"
        ) from exc
    if row.operation == "gitlab.merge_requests.create" and body.payload.get("patch_artifact") != row.payload.get(
        "patch_artifact"
    ):
        raise Problem("validation", "the patch of a merge request cannot be swapped in an edit")
    new = ProposedAction(
        id=new_id("act"),
        mission_id=row.mission_id,
        job_id=row.job_id,
        agent=row.agent,
        connector=row.connector,
        operation=row.operation,
        op_class=row.op_class,
        summary=row.summary,
        payload=body.payload,
        preview=build_preview(row.operation, body.payload, None)
        if row.operation != "gitlab.merge_requests.create"
        else row.preview,
        reversible=row.reversible,
        runs_as=row.runs_as,
        status="pending",
        required_approvals=row.required_approvals,
        four_eyes=row.four_eyes,
        idempotency_key=service.idempotency_key(
            row.mission_id, row.job_id, row.operation, {"edited_from": row.id, **body.payload}
        ),
        edited_from=row.id,
    )
    ctx.db.add(new)
    await ctx.db.flush()
    row.superseded_by = new.id
    await _set_status(ctx, row, "superseded")
    await events.append(
        ctx.db,
        row.mission_id,
        "action_proposed",
        {
            "action_id": new.id,
            "connector": new.connector,
            "operation": new.operation,
            "class": new.op_class,
            "summary": new.summary,
        },
        job_id=new.job_id,
        agent=new.agent,
    )
    await ctx.audit(
        "action.edited",
        "action",
        new.id,
        team=mission.team_id,
        before={"action": row.id},
        after={"operation": new.operation, "comment": body.comment},
    )
    return await action_out(ctx.db, new)


@router.operation("batchApproveActions")
async def batch_approve(ctx: CtxDep, body: m.BatchApprove) -> m.BatchResult:
    """Each item is independent: one refusal does not undo the others (each runs in a savepoint)."""
    results = []
    for action_id in body.action_ids[:200]:
        try:
            async with ctx.db.begin_nested():
                row, mission = await _action(ctx, action_id, lock=True)
                if row.op_class == OperationClass.destructive.value:
                    raise Problem("conflict", "destructive actions are approved one by one")
                await approve(ctx, row, mission, m.ApproveRequest(comment=body.comment))
            results.append(m.BatchItem(action_id=action_id, ok=True, status=row.status))
        except Problem as exc:
            results.append(m.BatchItem.model_validate({"action_id": action_id, "ok": False, "error": exc.body()}))
    for mission_id in {
        r.mission_id
        for r in (
            await ctx.db.execute(
                select(ProposedAction).where(ProposedAction.id.in_([x.action_id for x in results if x.ok]))
            )
        ).scalars()
    }:
        await _advance(ctx, mission_id)
    if not ctx.audited:
        await ctx.audit("action.batch_approve", "action", None, result="denied", after={"count": len(body.action_ids)})
    return m.BatchResult(results=results)


@router.operation("answerQuestion")
async def answer_question(ctx: CtxDep, id: str, body: m.AnswerRequest) -> m.Question:
    row = await ctx.db.get(Question, id, with_for_update=True)
    mission = await ctx.db.get(Mission, row.mission_id) if row else None
    if row is None or mission is None or not can_see(ctx, mission):
        raise Problem("not-found")
    if mission.created_by != ctx.user.user_id:
        ctx.require("questions:answer", mission.team_id)
    if row.answer is not None:
        raise Problem("conflict", "this question is already answered")
    if row.options and body.answer not in row.options:
        raise Problem("validation", f"answer must be one of: {', '.join(row.options)}")
    row.answer, row.answered_by, row.answered_at = body.answer, ctx.user.username, identity.now()
    job = await ctx.db.get(Job, row.job_id, with_for_update=True)
    await events.append(
        ctx.db,
        row.mission_id,
        "question_answered",
        {"question_id": row.id, "answer": body.answer, "answered_by": ctx.user.username},
        job_id=row.job_id,
        agent=row.agent,
    )
    if job is not None and job.status == JobStatus.needs_attention.value and job.needs_attention_reason == "question":
        still_open = (
            await ctx.db.execute(
                select(Question.id).where(Question.job_id == job.id, Question.answer.is_(None), Question.id != row.id)
            )
        ).first()
        if still_open is None:
            await service.set_job_status(ctx.db, job, JobStatus.running, "question answered")
    await ctx.audit(
        "question.answered",
        "question",
        row.id,
        team=mission.team_id,
        after={"mission": mission.id, "length": len(body.answer)},
    )
    await service.advance(ctx.db, ctx.state, mission)
    return question_out(row)


# ---------------------------------------------------------------- inbox


@router.operation("listReview")
async def list_review(
    ctx: CtxDep, cursor: str | None = None, limit: int = 50, kind: str | None = None, mission: str | None = None
) -> m.ReviewList:
    teams = ctx.user.teams_with("missions:read")
    scope = select(Mission.id)
    if teams is not None:
        scope = scope.where(or_(Mission.created_by == ctx.user.user_id, Mission.team_id.in_(sorted(teams))))
    if mission:
        scope = scope.where(Mission.id == mission)
    now = identity.now()
    items: list[tuple[str, m.ReviewItem]] = []
    titles: dict[str, str] = {}

    async def title(mission_id: str) -> str:
        if mission_id not in titles:
            row = await ctx.db.get(Mission, mission_id)
            titles[mission_id] = row.title if row else mission_id
        return titles[mission_id]

    actions = list(
        (
            await ctx.db.execute(
                select(ProposedAction).where(ProposedAction.status == "pending", ProposedAction.mission_id.in_(scope))
            )
        ).scalars()
    )
    questions = list(
        (
            await ctx.db.execute(select(Question).where(Question.answer.is_(None), Question.mission_id.in_(scope)))
        ).scalars()
    )
    suggestions = []
    if not mission:  # suggestions for agents whose team the reviewer may start missions for
        owners = {a.name: a.owner_team_id for a in (await ctx.db.execute(select(Agent))).scalars()}
        suggestions = [
            s
            for s in (
                await ctx.db.execute(select(MissionSuggestion).where(MissionSuggestion.status == "open"))
            ).scalars()
            if ctx.user.can("missions:start", owners.get(s.agent))
        ]
    counts = m.ReviewCounts(
        all=len(actions) + len(questions) + len(suggestions),
        writes=len(actions),
        questions=len(questions),
        suggestions=len(suggestions),
    )
    if kind in (None, "", "all", "writes", "action"):
        for a in actions:
            items.append(
                (
                    a.id,
                    m.ReviewItem(
                        kind="action",
                        action=await action_out(ctx.db, a),
                        mission_title=await title(a.mission_id),
                        age_s=int((now - a.created_at).total_seconds()),
                    ),
                )
            )
    if kind in (None, "", "all", "questions", "question"):
        for q in questions:
            items.append(
                (
                    q.id,
                    m.ReviewItem(
                        kind="question",
                        question=question_out(q),
                        mission_title=await title(q.mission_id),
                        age_s=int((now - q.created_at).total_seconds()),
                    ),
                )
            )
    if kind in (None, "", "all", "suggestions", "suggestion"):
        for s in suggestions:
            items.append(
                (
                    s.id,
                    m.ReviewItem.model_validate(
                        {
                            "kind": "suggestion",
                            "suggestion": {
                                "id": s.id,
                                "objective_id": s.objective_id,
                                "agent": s.agent,
                                "title": s.title,
                                "explanation": s.explanation,
                                "playbook": s.playbook,
                                "inputs": s.inputs,
                                "estimated_tokens": s.estimated_tokens,
                                "has_writes": s.has_writes,
                                "created_at": s.created_at,
                            },
                            "mission_title": s.title,
                            "age_s": int((now - s.created_at).total_seconds()),
                        }
                    ),
                )
            )
    items.sort(key=lambda pair: pair[0], reverse=True)
    if cursor:
        items = [p for p in items if p[0] < cursor]
    limit = max(1, min(limit, 200))
    next_cursor = items[limit - 1][0] if len(items) > limit else None
    return m.ReviewList(items=[i for _, i in items[:limit]], counts=counts, next_cursor=next_cursor)
