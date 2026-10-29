"""Objectives, assignments and mission suggestions (docs/03 §5 "Objectives", docs/04 §3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select
from z_common.models import api_missions as mm
from z_common.models import api_ops as m
from z_common.models.enums import ObjectiveStatus
from z_common.models.manifests import ObjectiveManifest

from z_server import objectives
from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.api.routes_catalog import _playbook, find_agent
from z_server.api.routes_missions import _create, mission_out
from z_server.db.models import Agent, Assignment, MissionSuggestion, Objective, ObjectiveCheck, User
from z_server.errors import Problem
from z_server.ids import new_id

router = ContractRouter()
TREND_POINTS = 20
EDITABLE = ("title", "metric", "cadence", "allowed_playbooks", "mode", "budget_per_period")


async def objective_out(ctx: Ctx, row: Objective) -> m.Objective:
    trend = [
        v
        for (v,) in (
            await ctx.db.execute(
                select(ObjectiveCheck.value)
                .where(ObjectiveCheck.objective_id == row.id)
                .order_by(ObjectiveCheck.ts.desc())
                .limit(TREND_POINTS)
            )
        ).all()
    ][::-1]
    owner = await ctx.db.get(User, row.owner_user_id)
    return m.Objective.model_validate(
        {
            **row.manifest,
            "id": row.id,
            "status": row.status,
            "last_value": row.last_value,
            "last_checked_at": row.last_checked_at,
            "trend": trend,
            "owner_user": owner.username if owner else None,
        }
    )


def check_out(row: ObjectiveCheck) -> m.ObjectiveCheck:
    return m.ObjectiveCheck.model_validate(
        {
            "ts": row.ts,
            "value": row.value,
            "target": row.target,
            "status": row.status,
            "explanation": row.explanation,
            "suggestions": list(row.suggestions or []),
            "started_missions": list(row.started_missions or []),
        }
    )


async def _objective(ctx: Ctx, objective_id: str, write: bool = False) -> tuple[Objective, Agent]:
    row = await ctx.db.get(Objective, objective_id, with_for_update=write)
    agent = await ctx.db.get(Agent, row.agent_id) if row else None
    if row is None or agent is None:
        raise Problem("not-found", "no such objective")
    if write:
        ctx.require("agents:write", agent.owner_team_id)
    return row, agent


async def _check_playbooks(ctx: Ctx, names: list[str]) -> None:
    for name in names:
        await _playbook(ctx, name)


@router.operation("listObjectives")
async def list_objectives(ctx: CtxDep, id: str) -> list[dict[str, Any]]:
    agent = await find_agent(ctx, id)
    rows = (
        await ctx.db.execute(select(Objective).where(Objective.agent_id == agent.id).order_by(Objective.id))
    ).scalars()
    return [(await objective_out(ctx, r)).dump() for r in rows]


@router.operation("createObjective", status_code=201)
async def create_objective(ctx: CtxDep, id: str, body: dict[str, Any]) -> m.Objective:
    agent = await find_agent(ctx, id)
    ctx.require("agents:write", agent.owner_team_id)
    body = {**body, "agent": agent.name}
    body.pop("status", None)
    body.pop("owner_user", None)
    try:
        manifest = ObjectiveManifest.model_validate(body)
    except ValidationError as exc:
        raise Problem(
            "validation", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
        ) from exc
    objectives.validate_cadence(manifest)
    await _check_playbooks(ctx, manifest.allowed_playbooks)
    now = datetime.now(UTC)
    row = Objective(
        id=new_id("obj"),
        agent_id=agent.id,
        manifest=manifest.dump(),
        status=ObjectiveStatus.on_track.value,
        owner_user_id=ctx.user.user_id,
        next_check_at=None if objectives.next_check(manifest, now) is None else now,  # first check on the next tick
    )
    ctx.db.add(row)
    await ctx.db.flush()
    await ctx.audit("objective.created", "objective", row.id, team=agent.owner_team_id, after=row.manifest)
    return await objective_out(ctx, row)


@router.operation("patchObjective")
async def patch_objective(ctx: CtxDep, id: str, body: m.ObjectivePatch) -> m.Objective:
    row, agent = await _objective(ctx, id, write=True)
    before = {**row.manifest, "status": row.status}
    changes = body.model_dump(exclude_unset=True)
    merged = {**row.manifest, **{k: changes[k] for k in EDITABLE if k in changes}}
    try:
        manifest = ObjectiveManifest.model_validate(merged)
    except ValidationError as exc:
        raise Problem(
            "validation", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
        ) from exc
    objectives.validate_cadence(manifest)
    await _check_playbooks(ctx, manifest.allowed_playbooks)
    row.manifest = manifest.dump()
    now = datetime.now(UTC)
    if changes.get("paused") is True:
        row.status, row.next_check_at = ObjectiveStatus.paused.value, None
    elif changes.get("paused") is False and row.status == ObjectiveStatus.paused.value:
        row.status = ObjectiveStatus.on_track.value
        row.next_check_at = None if objectives.next_check(manifest, now) is None else now
    elif "cadence" in changes and row.status != ObjectiveStatus.paused.value:
        row.next_check_at = objectives.next_check(manifest, now)
    await ctx.db.flush()
    await ctx.audit(
        "objective.updated",
        "objective",
        row.id,
        team=agent.owner_team_id,
        before=before,
        after={**row.manifest, "status": row.status},
    )
    return await objective_out(ctx, row)


@router.operation("objectiveHistory")
async def objective_history(ctx: CtxDep, id: str) -> list[dict[str, Any]]:
    await _objective(ctx, id)
    rows = (
        await ctx.db.execute(
            select(ObjectiveCheck)
            .where(ObjectiveCheck.objective_id == id)
            .order_by(ObjectiveCheck.ts.desc())
            .limit(200)
        )
    ).scalars()
    return [check_out(r).dump() for r in rows]


@router.operation("evaluateObjective")
async def evaluate_objective(ctx: CtxDep, id: str) -> m.ObjectiveCheck:
    row, _agent = await _objective(ctx, id, write=True)
    if row.status == ObjectiveStatus.paused.value:
        raise Problem("conflict", "the objective is paused")
    ctx.audited = True  # objectives.evaluate writes the audit event
    return check_out(await objectives.evaluate(ctx.db, ctx.state, row, ctx.actor()))


# ---------------------------------------------------------------- assignments


def assignment_out(row: Assignment, agent: Agent) -> m.Assignment:
    return m.Assignment.model_validate(
        {
            "id": row.id,
            "agent": agent.name,
            "object_kind": row.object_kind,
            "object_ref": row.object_ref,
            "role": row.role,
            "since": row.since,
        }
    )


@router.operation("listAssignments")
async def list_assignments(ctx: CtxDep, id: str) -> list[dict[str, Any]]:
    agent = await find_agent(ctx, id)
    rows = (
        await ctx.db.execute(select(Assignment).where(Assignment.agent_id == agent.id).order_by(Assignment.id))
    ).scalars()
    return [assignment_out(r, agent).dump() for r in rows]


@router.operation("createAssignment", status_code=201)
async def create_assignment(ctx: CtxDep, id: str, body: m.AssignmentCreate) -> m.Assignment:
    agent = await find_agent(ctx, id)
    ctx.require("agents:write", agent.owner_team_id)
    ref = body.object_ref.strip()
    if not ref:
        raise Problem("validation", "object_ref is empty")
    existing = (
        await ctx.db.execute(
            select(Assignment).where(
                Assignment.agent_id == agent.id,
                Assignment.object_kind == body.object_kind.value,
                Assignment.object_ref == ref,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Problem("conflict", "the agent already looks after this object")
    row = Assignment(
        id=new_id("asg"), agent_id=agent.id, object_kind=body.object_kind.value, object_ref=ref, role=body.role
    )
    ctx.db.add(row)
    await ctx.db.flush()
    await ctx.audit(
        "assignment.created",
        "agent",
        agent.id,
        team=agent.owner_team_id,
        after={"object_kind": row.object_kind, "object_ref": ref, "role": row.role},
    )
    return assignment_out(row, agent)


# ---------------------------------------------------------------- suggestions


async def _suggestion(ctx: Ctx, suggestion_id: str) -> tuple[MissionSuggestion, Agent | None]:
    row = await ctx.db.get(MissionSuggestion, suggestion_id, with_for_update=True)
    if row is None:
        raise Problem("not-found", "no such suggestion")
    agent = (await ctx.db.execute(select(Agent).where(Agent.name == row.agent))).scalar_one_or_none()
    if not ctx.user.can("missions:start", agent.owner_team_id if agent else None):
        raise Problem("not-found", "no such suggestion")
    if row.status != "open":
        raise Problem("conflict", f"the suggestion is {row.status}")
    return row, agent


@router.operation("acceptSuggestion")
async def accept_suggestion(ctx: CtxDep, id: str, body: dict[str, Any]) -> mm.Mission:
    row, _agent = await _suggestion(ctx, id)
    if row.playbook:
        create = mm.MissionCreate(playbook=row.playbook, inputs=row.inputs, title=row.title[:200])
    else:
        create = mm.MissionCreate(
            prompt=str(row.inputs.get("prompt") or row.explanation), title=row.title[:200], agents=[row.agent]
        )
    mission = await _create(ctx, create)
    row.status, row.mission_id = "accepted", mission.id
    await ctx.audit("suggestion.accepted", "suggestion", row.id, team=mission.team_id, after={"mission": mission.id})
    return await mission_out(ctx, mission)


@router.operation("dismissSuggestion", status_code=204)
async def dismiss_suggestion(ctx: CtxDep, id: str, body: m.ProposalDecision) -> Response:
    row, agent = await _suggestion(ctx, id)
    row.status = "dismissed"
    await ctx.audit(
        "suggestion.dismissed",
        "suggestion",
        row.id,
        team=agent.owner_team_id if agent else None,
        after={"reason": body.reason},
    )
    return Response(status_code=204)
