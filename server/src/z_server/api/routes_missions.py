"""Missions, jobs, artifacts and live events (docs/03 §5 "Missions", "Jobs"; docs/04)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import File, Form, Header, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import Select, func, or_, select
from z_common.models import api_catalog as cm
from z_common.models import api_missions as m
from z_common.models.enums import DataClass, JobStatus, MissionStatus, StepStatus
from z_common.models.manifests import MissionPlan, PlanStep

from z_server import artifacts, events
from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.api.paging import page
from z_server.api.routes_catalog import _playbook, find_agent, insert_playbook
from z_server.api.routes_internal import artifact_out, job_out
from z_server.db.models import Artifact, ChatSession, Job, JobMessage, Mission, ProposedAction, Question, Team, User
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.missions import service, takeover
from z_server.state import AppState

router = ContractRouter()
SSE_KEEPALIVE_S = 15.0
ACTIVE = {MissionStatus.queued.value, MissionStatus.running.value, MissionStatus.waiting.value}
EDITABLE = {MissionStatus.draft.value, MissionStatus.planned.value}


# ---------------------------------------------------------------- visibility


def visible(ctx: Ctx, stmt: Select[Any]) -> Select[Any]:
    """missions:read is team-scoped: org-wide grants see everything, team grants their teams, and everyone their own."""
    teams = ctx.user.teams_with("missions:read")
    if teams is None:
        return stmt
    return stmt.where(or_(Mission.created_by == ctx.user.user_id, Mission.team_id.in_(sorted(teams))))


def can_see(ctx: Ctx, mission: Mission) -> bool:
    return mission.created_by == ctx.user.user_id or ctx.user.can_on("missions:read", mission.team_id)


async def get_mission(ctx: Ctx, mission_id: str, *, lock: bool = False, control: str | None = None) -> Mission:
    mission = await ctx.db.get(Mission, mission_id, with_for_update=lock)
    if mission is None or not can_see(ctx, mission):
        raise Problem("not-found")
    if control and mission.created_by != ctx.user.user_id:
        ctx.require(control, mission.team_id)
    return mission


async def get_job(ctx: Ctx, job_id: str, *, lock: bool = False) -> tuple[Job, Mission]:
    job = await ctx.db.get(Job, job_id, with_for_update=lock)
    if job is None:
        raise Problem("not-found")
    return job, await get_mission(ctx, job.mission_id)


# ---------------------------------------------------------------- output


async def mission_out(ctx: Ctx, mission: Mission) -> m.Mission:
    counts = dict(
        (
            await ctx.db.execute(
                select(Job.status, func.count()).where(Job.mission_id == mission.id).group_by(Job.status)
            )
        ).all()
    )
    pending_actions = (
        await ctx.db.execute(
            select(func.count())
            .select_from(ProposedAction)
            .where(ProposedAction.mission_id == mission.id, ProposedAction.status == "pending")
        )
    ).scalar_one()
    pending_questions = (
        await ctx.db.execute(
            select(func.count())
            .select_from(Question)
            .where(Question.mission_id == mission.id, Question.answer.is_(None))
        )
    ).scalar_one()
    creator = await ctx.db.get(User, mission.created_by)
    team = await ctx.db.get(Team, mission.team_id) if mission.team_id else None
    jobs = m.JobCounts(
        total=sum(counts.values()),
        queued=counts.get("queued", 0),
        running=counts.get("running", 0),
        needs_attention=counts.get("needs_attention", 0),
        succeeded=counts.get("succeeded", 0),
        failed=sum(counts.get(s, 0) for s in ("failed", "timed_out", "budget_exceeded")),
        cancelled=counts.get("cancelled", 0),
    )
    return m.Mission.model_validate(
        {
            "id": mission.id,
            "title": mission.title,
            "prompt": mission.prompt,
            "playbook_id": mission.playbook_id,
            "playbook": mission.playbook_name,
            "playbook_version": mission.playbook_version,
            "plan": mission.plan,
            "inputs": mission.inputs,
            "connectors": mission.connectors,
            "data_class": mission.data_class,
            "autonomy": mission.autonomy,
            "plan_first": mission.plan_first,
            "status": mission.status,
            "waiting_reason": mission.waiting_reason,
            "created_by": creator.username if creator else mission.created_by,
            "team": team.name if team else None,
            "session_id": mission.session_id,
            "trigger_id": mission.trigger_id,
            "budget": mission.budget,
            "tokens_in": mission.tokens_in,
            "tokens_out": mission.tokens_out,
            "jobs": jobs,
            "pending_actions": pending_actions,
            "pending_questions": pending_questions,
            "created_at": mission.created_at,
            "started_at": mission.started_at,
            "ended_at": mission.ended_at,
            "error": mission.error,
        }
    )


# ---------------------------------------------------------------- create, start


async def _create(ctx: Ctx, body: m.MissionCreate, *, playbook_ref: str | None = None) -> Mission:
    ref = playbook_ref or body.playbook
    playbook = await _playbook(ctx, ref) if ref else None
    if body.session_id:
        owned = await ctx.db.get(ChatSession, body.session_id)
        if owned is None or owned.user_id != ctx.user.user_id:
            raise Problem("not-found", "no such session")
    plan = body.plan
    if playbook is None and plan is None and body.agents:
        if len(body.agents) != 1 or not body.prompt:
            raise Problem(
                "validation", "a free-form mission with several agents needs a plan (POST /missions/draft-plan)"
            )
        agent = await find_agent(ctx, body.agents[0])  # a handle (@atlas) or a name
        plan = MissionPlan(steps=[PlanStep(id="main", agent=agent.name, prompt=body.prompt)])
    if playbook is None and not body.prompt and plan is not None and not body.title:
        raise Problem("validation", "a mission without a playbook needs a prompt or a title")
    request = service.MissionRequest(
        title=body.title,
        prompt=body.prompt,
        playbook=playbook,
        inputs=body.inputs or {},
        plan=plan,
        connectors=list(body.connectors or []),
        data_class=body.data_class,
        autonomy=body.autonomy,
        plan_first=bool(body.plan_first),
        budget=body.budget.dump() if body.budget else {},
        session_id=body.session_id,
    )
    mission = await service.create(ctx.db, ctx.state, ctx.user, request)
    await ctx.audit(
        "mission.created",
        "mission",
        mission.id,
        team=mission.team_id,
        after={
            "title": mission.title,
            "playbook": mission.playbook_name,
            "data_class": mission.data_class,
            "autonomy": mission.autonomy,
        },
    )
    if body.start and not mission.plan_first:
        await service.start(ctx.db, ctx.state, mission)
        await ctx.audit("mission.started", "mission", mission.id, team=mission.team_id)
    return mission


@router.operation("createMission", status_code=201)
async def create_mission(ctx: CtxDep, body: m.MissionCreate) -> m.Mission:
    return await mission_out(ctx, await _create(ctx, body))


@router.operation("draftPlan")
async def draft_plan(ctx: CtxDep, body: m.DraftPlanRequest) -> m.DraftPlan:
    from z_server.missions import planner

    result = await planner.draft(ctx.db, ctx.state, ctx.user, body.prompt, body.agents, body.connectors)
    return m.DraftPlan.model_validate(
        {
            "plan": result.plan.dump(),
            "suggested_playbook": result.suggested_playbook,
            "parsed": result.parsed,
            "estimated_tokens": result.estimated_tokens,
        }
    )


@router.operation("runPlaybook", status_code=201)
async def run_playbook(ctx: CtxDep, id: str, body: m.PlaybookRun) -> m.Mission:
    create = m.MissionCreate(
        inputs=body.inputs, title=body.title, data_class=body.data_class, plan_first=body.plan_first, budget=body.budget
    )
    return await mission_out(ctx, await _create(ctx, create, playbook_ref=id))


@router.operation("listMissions")
async def list_missions(
    ctx: CtxDep,
    cursor: str | None = None,
    limit: int = 50,
    status: str | None = None,
    playbook: str | None = None,
    agent: str | None = None,
    created_by: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    stmt = visible(ctx, select(Mission)).where(Mission.eval_run_id.is_(None))  # eval cases live in their run
    if status:
        stmt = stmt.where(Mission.status.in_(status.split(",")))
    if playbook:
        stmt = stmt.where(Mission.playbook_name == playbook)
    if agent:
        stmt = stmt.where(Mission.id.in_(select(Job.mission_id).where(Job.agent_name == agent.removeprefix("@"))))
    if created_by:
        user = (
            await ctx.db.execute(select(User).where(or_(User.username == created_by, User.id == created_by)))
        ).scalar_one_or_none()
        stmt = stmt.where(Mission.created_by == (user.id if user else created_by))
    if q:
        stmt = stmt.where(or_(Mission.title.ilike(f"%{q}%"), Mission.prompt.ilike(f"%{q}%")))
    rows, next_cursor = await page(ctx.db, stmt, Mission.id, cursor, limit)
    return {"items": [(await mission_out(ctx, r)).dump() for r in rows], "next_cursor": next_cursor}


@router.operation("getMission")
async def get_mission_route(ctx: CtxDep, id: str) -> m.Mission:
    return await mission_out(ctx, await get_mission(ctx, id))


@router.operation("patchMissionPlan")
async def patch_plan(ctx: CtxDep, id: str, body: m.PlanPatch) -> m.Mission:
    mission = await get_mission(ctx, id, lock=True, control="missions:start")
    if mission.status not in EDITABLE:
        raise Problem("conflict", f"the plan can only be edited before start (mission is {mission.status})")
    if mission.playbook_id:
        raise Problem(
            "conflict", "a playbook mission follows its playbook; edit the playbook or start a free-form mission"
        )
    plan = body.plan
    for step in plan.steps:
        step.status = StepStatus.pending
    await service.check_plan(ctx.db, plan)
    before = mission.plan
    mission.plan = plan.dump()
    await events.append(ctx.db, mission.id, "plan", {"steps": [s.dump() for s in plan.steps], "writes": plan.writes})
    await ctx.audit(
        "mission.plan_edited",
        "mission",
        mission.id,
        team=mission.team_id,
        before={"steps": len((before or {}).get("steps", []))},
        after={"steps": len(plan.steps)},
    )
    return await mission_out(ctx, mission)


@router.operation("startMission")
async def start_mission(ctx: CtxDep, id: str, body: m.MissionControl) -> m.Mission:
    mission = await get_mission(ctx, id, lock=True, control="missions:start")
    await service.start(ctx.db, ctx.state, mission)
    await ctx.audit("mission.started", "mission", mission.id, team=mission.team_id, after={"reason": body.reason})
    return await mission_out(ctx, mission)


@router.operation("pauseMission")
async def pause_mission(ctx: CtxDep, id: str, body: m.MissionControl) -> m.Mission:
    mission = await get_mission(ctx, id, lock=True, control="missions:control")
    if mission.status not in ACTIVE:
        raise Problem("conflict", f"mission is {mission.status}")
    await service.pause(ctx.db, mission, body.reason or "paused by " + ctx.user.username)
    await ctx.audit("mission.paused", "mission", mission.id, team=mission.team_id, after={"reason": body.reason})
    return await mission_out(ctx, mission)


@router.operation("resumeMission")
async def resume_mission(ctx: CtxDep, id: str, body: m.MissionControl) -> m.Mission:
    mission = await get_mission(ctx, id, lock=True, control="missions:control")
    if mission.status != MissionStatus.paused.value and not await service.has_paused_jobs(ctx.db, mission):
        raise Problem("conflict", f"mission is {mission.status}")
    await service.resume(ctx.db, ctx.state, mission)
    await ctx.audit("mission.resumed", "mission", mission.id, team=mission.team_id, after={"reason": body.reason})
    return await mission_out(ctx, mission)


@router.operation("cancelMission")
async def cancel_mission(ctx: CtxDep, id: str, body: m.MissionControl) -> m.Mission:
    mission = await get_mission(ctx, id, lock=True, control="missions:control")
    if MissionStatus(mission.status).terminal:
        raise Problem("conflict", f"mission is {mission.status}")
    await service.finish(
        ctx.db, ctx.state, mission, MissionStatus.cancelled, body.reason or f"cancelled by {ctx.user.username}"
    )
    await ctx.audit("mission.cancelled", "mission", mission.id, team=mission.team_id, after={"reason": body.reason})
    return await mission_out(ctx, mission)


@router.operation("guideMission", status_code=202)
async def guide_mission(ctx: CtxDep, id: str, body: m.GuideMessage) -> Response:
    mission = await get_mission(ctx, id, control="missions:control")
    stmt = select(Job).where(
        Job.mission_id == mission.id,
        Job.status.in_([JobStatus.running.value, JobStatus.needs_attention.value, JobStatus.paused.value]),
    )
    if body.job_id:
        stmt = stmt.where(Job.id == body.job_id)
    jobs = list((await ctx.db.execute(stmt)).scalars())
    if not jobs:
        raise Problem("conflict", "no running job to guide" + (f" with id {body.job_id}" if body.job_id else ""))
    for job in jobs:
        ctx.db.add(JobMessage(id=new_id("jmsg"), job_id=job.id, text=body.text, by=ctx.user.username))
        await events.append(
            ctx.db,
            mission.id,
            "message",
            {"role": "user", "text": body.text, "by": ctx.user.username},
            job_id=job.id,
            agent=job.agent_name,
            step_id=job.step_id,
        )
    await ctx.audit(
        "mission.guided",
        "mission",
        mission.id,
        team=mission.team_id,
        after={"jobs": [j.id for j in jobs], "length": len(body.text)},
    )
    return Response(status_code=202)


@router.operation("uploadMissionFile", status_code=201)
async def upload_mission_file(
    ctx: CtxDep, id: str, file: Annotated[UploadFile, File()], name: Annotated[str | None, Form()] = None
) -> m.Artifact:
    mission = await get_mission(ctx, id, lock=True, control="missions:start")
    if mission.status not in EDITABLE:
        raise Problem("conflict", f"files can only be added before start (mission is {mission.status})")
    data = await file.read(artifacts.MAX_ARTIFACT_BYTES + 1)
    try:
        row = await artifacts.create(
            ctx.db,
            ctx.state.settings.artifacts_dir,
            mission_id=mission.id,
            job_id=None,
            kind="data",
            name=name or file.filename or "file",
            data=data,
            data_class=mission.data_class,
            content_type=file.content_type,
        )
    except artifacts.ArtifactTooLarge as exc:
        raise Problem("validation", str(exc), status=413) from exc
    await ctx.audit(
        "mission.file_uploaded",
        "artifact",
        row.id,
        team=mission.team_id,
        after={"mission": mission.id, "name": row.name, "size": row.size, "sha256": row.sha256},
    )
    return artifact_out(row)


# ---------------------------------------------------------------- jobs, artifacts, replay


@router.operation("listMissionJobs")
async def list_mission_jobs(
    ctx: CtxDep,
    id: str,
    cursor: str | None = None,
    limit: int = 100,
    status: str | None = None,
    step: str | None = None,
) -> dict[str, Any]:
    mission = await get_mission(ctx, id)
    stmt = select(Job).where(Job.mission_id == mission.id)
    if status:
        stmt = stmt.where(Job.status.in_(status.split(",")))
    if step:
        stmt = stmt.where(Job.step_id == step)
    rows, next_cursor = await page(ctx.db, stmt, Job.id, cursor, limit)
    return {"items": [job_out(r).dump() for r in rows], "next_cursor": next_cursor}


@router.operation("getJob")
async def get_job_route(ctx: CtxDep, id: str) -> m.Job:
    job, _ = await get_job(ctx, id)
    return job_out(job)


@router.operation("retryJob")
async def retry_job(ctx: CtxDep, id: str, body: m.MissionControl) -> m.Job:
    job, mission = await get_job(ctx, id, lock=True)
    if mission.created_by != ctx.user.user_id:
        ctx.require("missions:control", mission.team_id)
    if job.status not in {
        JobStatus.failed.value,
        JobStatus.timed_out.value,
        JobStatus.cancelled.value,
        JobStatus.needs_attention.value,
    }:
        raise Problem("conflict", f"job is {job.status}")
    await service.retry_job(ctx.db, ctx.state, mission, job)
    await ctx.audit(
        "job.retried", "job", job.id, team=mission.team_id, after={"attempt": job.attempt, "reason": body.reason}
    )
    return job_out(job)


@router.operation("listJobArtifacts")
async def list_job_artifacts(ctx: CtxDep, id: str) -> list[m.Artifact]:
    job, _ = await get_job(ctx, id)
    rows = (
        await ctx.db.execute(select(Artifact).where(Artifact.job_id == job.id).order_by(Artifact.created_at))
    ).scalars()
    return [artifact_out(r) for r in rows]


@router.operation("getArtifact")
async def get_artifact(ctx: CtxDep, id: str, meta: bool = False) -> Response:
    row = await ctx.db.get(Artifact, id)
    if row is None:
        raise Problem("not-found")
    await get_mission(ctx, row.mission_id)
    if meta:
        return Response(json.dumps(artifact_out(row).dump(), default=str), media_type="application/json")
    if DataClass(row.data_class).rank >= DataClass.C2.rank:
        await ctx.audit(
            "artifact.downloaded", "artifact", row.id, after={"mission": row.mission_id, "data_class": row.data_class}
        )
    path = Path(row.storage_path)
    if not path.is_file():
        raise Problem("not-found", "artifact content is missing from storage")
    headers = {
        "Content-Disposition": f'attachment; filename="{artifacts.safe_name(row.name)}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    return Response(path.read_bytes(), media_type="application/octet-stream", headers=headers)


@router.operation("getMissionReplay")
async def get_replay(ctx: CtxDep, id: str) -> m.Replay:
    mission = await get_mission(ctx, id)
    rows, after = [], 0
    while True:
        batch = await events.since(ctx.db, mission.id, after, limit=1000)
        rows += batch
        if len(batch) < 1000:
            break
        after = batch[-1].seq
    arts = (
        await ctx.db.execute(select(Artifact).where(Artifact.mission_id == mission.id).order_by(Artifact.created_at))
    ).scalars()
    return m.Replay.model_validate(
        {
            "mission": (await mission_out(ctx, mission)).dump(),
            "events": [events.as_contract(e) for e in rows],
            "artifacts": [artifact_out(a).dump() for a in arts],
        }
    )


# ---------------------------------------------------------------- live events (SSE)


def _sse(row: Any) -> str:
    data = json.dumps(events.as_contract(row), separators=(",", ":"), default=str)
    return f"id: {row.seq}\nevent: {row.type}\ndata: {data}\n\n"


async def _stream(state: AppState, mission_id: str, after: int, job_id: str | None) -> AsyncIterator[str]:
    """Own short transactions per poll; wakes on Postgres NOTIFY, keeps the connection alive with comments."""
    yield "retry: 3000\n\n"
    while True:
        async with state.sessions() as db:
            rows = await events.since(db, mission_id, after, job_id=job_id)
            mission = await db.get(Mission, mission_id)
            done = mission is None or MissionStatus(mission.status).terminal
        for row in rows:
            after = row.seq
            yield _sse(row)
        if rows and len(rows) >= 500:
            continue
        if done:
            return
        await state.hub.wait(mission_id, timeout=SSE_KEEPALIVE_S)
        yield ": keepalive\n\n"


def _after(after_seq: int | None, last_event_id: str | None) -> int:
    if last_event_id and last_event_id.isdigit():
        return int(last_event_id)
    return max(0, after_seq or 0)


SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


async def _ended(ctx: Ctx, mission: Mission, after: int, job_id: str | None) -> bool:
    """The mission has ended and nothing is left after `after`: answer 204 so the client stops reconnecting."""
    if not MissionStatus(mission.status).terminal or after == 0:
        return False
    return not await events.since(ctx.db, mission.id, after, job_id=job_id, limit=1)


@router.operation("streamMissionEvents")
async def stream_mission_events(
    ctx: CtxDep, id: str, after_seq: int | None = None, last_event_id: Annotated[str | None, Header()] = None
) -> Response:
    mission = await get_mission(ctx, id)
    after = _after(after_seq, last_event_id)
    if await _ended(ctx, mission, after, None):
        return Response(status_code=204)
    return StreamingResponse(
        _stream(ctx.state, mission.id, after, None),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.operation("streamJobEvents")
async def stream_job_events(ctx: CtxDep, id: str, last_event_id: Annotated[str | None, Header()] = None) -> Response:
    job, mission = await get_job(ctx, id)
    after = _after(None, last_event_id)
    if await _ended(ctx, mission, after, job.id):
        return Response(status_code=204)
    return StreamingResponse(
        _stream(ctx.state, mission.id, after, job.id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


# ---------------------------------------------------------------- save as playbook

STEP_KEYS = (
    "id",
    "title",
    "agent",
    "depends_on",
    "fanout",
    "inputs_from",
    "when",
    "output",
    "writes",
    "workspace",
    "repo",
    "credentials",
    "model",
)


@router.operation("saveMissionAsPlaybook", status_code=201)
async def save_mission_as_playbook(ctx: CtxDep, id: str, body: m.SaveAsPlaybook) -> cm.Playbook:
    """The mission's plan becomes a reviewed, versioned playbook (docs/09 "New mission": Save as playbook)."""
    mission = await get_mission(ctx, id)
    if mission.team_id:
        ctx.require("playbooks:write", mission.team_id)
    plan = MissionPlan.model_validate(mission.plan)
    steps = []
    for step in plan.steps:
        raw = step.dump()
        steps.append({**{k: raw[k] for k in STEP_KEYS if k in raw}, "prompt": step.effective_prompt})
    budget = {k: v for k, v in (mission.budget or {}).items() if k in {"max_tokens", "timeout_s"} and v}
    manifest: dict[str, Any] = {
        "name": body.name,
        "version": "1.0.0",
        "description": (body.description or mission.title)[:500],
        "team": sorted({s.agent for s in plan.steps}),
        "connectors": list(mission.connectors or []),
        "steps": steps,
        "autonomy": mission.autonomy,
        "four_eyes": mission.four_eyes,
        "data_class": mission.data_class,
    }
    if body.domain:
        manifest["domain"] = body.domain
    if budget:
        manifest["budget"] = budget
    return await insert_playbook(ctx, manifest, mission.team_id, {"from_mission": mission.id})


@router.operation("takeOverMission")
async def take_over_mission(ctx: CtxDep, id: str, body: m.TakeOver) -> m.TakeOverSession:
    """Pause the job and open its workspace in the web app (files; edits in read_write), ADR 0019."""
    mission = await get_mission(ctx, id, control="missions:control")
    job = await ctx.db.get(Job, body.job_id, with_for_update=True)
    if job is None or job.mission_id != mission.id:
        raise Problem("not-found", "no such job in this mission")
    value = await takeover.start(ctx.db, mission, job, ctx.user.username, body.mode)
    await ctx.audit("mission.taken_over", "job", job.id, after={"mission": mission.id, "mode": body.mode})
    return m.TakeOverSession(
        job_id=job.id,
        terminal_url=f"{ctx.state.settings.public_url}/missions/{mission.id}/take-over/{job.id}",
        expires_at=datetime.fromisoformat(value["expires_at"]),
        mode=body.mode,
    )


async def _take_over_job(ctx: Ctx, id: str, job_id: str) -> tuple[Mission, Job]:
    mission = await get_mission(ctx, id, control="missions:control")
    job = await ctx.db.get(Job, job_id, with_for_update=True)
    if job is None or job.mission_id != mission.id:
        raise Problem("not-found", "no such job in this mission")
    return mission, job


@router.operation("getTakeOverWorkspace")
async def get_take_over_workspace(ctx: CtxDep, id: str, job_id: str) -> m.TakeOverWorkspace:
    mission, job = await _take_over_job(ctx, id, job_id)
    _, out = await takeover.view(ctx.db, ctx.state, mission, job, ctx.user.username)
    return m.TakeOverWorkspace.model_validate(out)


@router.operation("getTakeOverFile")
async def get_take_over_file(ctx: CtxDep, id: str, job_id: str, path: str = "") -> m.TakeOverFileContent:
    mission, job = await _take_over_job(ctx, id, job_id)
    return m.TakeOverFileContent.model_validate(
        await takeover.read(ctx.db, ctx.state, mission, job, ctx.user.username, path)
    )


@router.operation("putTakeOverFile")
async def put_take_over_file(ctx: CtxDep, id: str, job_id: str, body: m.TakeOverEdit) -> m.TakeOverFile:
    _, job = await _take_over_job(ctx, id, job_id)
    size = await takeover.write(ctx.db, job, ctx.user.username, body.path, body.content, body.encoding)
    path = takeover.clean_path(body.path)
    await ctx.audit("mission.take_over_edit", "job", job.id, after={"path": path, "size": size})
    return m.TakeOverFile(path=path, size=size, origin="human", deleted=False)


@router.operation("deleteTakeOverFile", status_code=204)
async def delete_take_over_file(ctx: CtxDep, id: str, job_id: str, path: str = "") -> Response:
    _, job = await _take_over_job(ctx, id, job_id)
    await takeover.write(ctx.db, job, ctx.user.username, path, None, "utf-8")
    await ctx.audit("mission.take_over_edit", "job", job.id, after={"path": takeover.clean_path(path), "deleted": True})
    return Response(status_code=204)


@router.operation("finishTakeOver")
async def finish_take_over(ctx: CtxDep, id: str, job_id: str, body: m.TakeOverFinish) -> m.TakeOverResult:
    mission, job = await _take_over_job(ctx, id, job_id)
    result = await takeover.finish(ctx.db, ctx.state, mission, job, ctx.user.username, body.resume)
    await ctx.audit(
        "mission.take_over_finished",
        "job",
        job.id,
        after={"edited": result["edited"], "restarted": result["restarted"], "resumed": result["resumed"]},
    )
    return m.TakeOverResult.model_validate(result)
