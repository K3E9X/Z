"""Runner protocol endpoints (contracts/README.md "Runner protocol", docs/04)."""

from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
from typing import Annotated, Any

import jsonschema
from fastapi import File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from z_common.models import api_internal as m
from z_common.models import api_missions as mm
from z_common.models.actions import BUILTIN_PAYLOADS
from z_common.models.enums import ActionStatus, JobStatus, JudgeVerdict, OperationClass
from z_common.models.manifests import MissionPlan

from z_server import artifacts, audit, events, memory
from z_server.actions.previews import build as build_preview
from z_server.api.internal import InternalRouter, JobCtx, JobCtxDep, _bearer, require_service
from z_server.crypto import new_token, token_hash
from z_server.db.models import (
    Agent,
    Artifact,
    Connector,
    Credential,
    Job,
    JobMessage,
    Mission,
    Operation,
    ProposedAction,
    Question,
    User,
)
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.llm.litellm import LlmKeyError
from z_server.missions import service, specs, workspace
from z_server.missions.service import utcnow
from z_server.state import AppState

router = InternalRouter()
MAX_WAIT_S = 30


def _secrets(ctx: JobCtx) -> list[str]:
    try:
        return [service.mission_key(ctx.state, ctx.mission)]
    except Problem:
        return []


def _step(mission: Mission, step_id: str) -> dict[str, Any]:
    for step in MissionPlan.model_validate(mission.plan).steps:
        if step.id == step_id:
            return step.dump()
    return {}


async def control_for(db: Any, job: Job, *, deliver: bool = True) -> m.JobControl:
    messages = (
        (
            await db.execute(
                select(JobMessage)
                .where(JobMessage.job_id == job.id, JobMessage.delivered_at.is_(None))
                .order_by(JobMessage.created_at)
            )
        )
        .scalars()
        .all()
    )
    answers = (
        (
            await db.execute(
                select(Question).where(
                    Question.job_id == job.id, Question.answer.is_not(None), Question.delivered_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    actions = (await db.execute(select(ProposedAction).where(ProposedAction.job_id == job.id))).scalars().all()
    if deliver:
        at = utcnow()
        for row in [*messages, *answers]:
            row.delivered_at = at
    return m.JobControl(
        state=job.control_state,
        reason=job.control_reason,
        messages=[m.ControlMessage(id=x.id, text=x.text, by=x.by) for x in messages],
        answers=[m.Answer(question_id=q.id, answer=q.answer or "", answered_by=q.answered_by or "") for q in answers],
        actions=[m.ActionState(action_id=a.id, status=ActionStatus(a.status)) for a in actions],
    )


@router.operation("internalJobSpec")
async def job_spec(ctx: JobCtxDep, id: str) -> JSONResponse:
    session = new_token("zr")
    job = ctx.job
    job.bootstrap_token_hash = None  # single use
    job.session_token_hash = token_hash(session)
    deadline = job.deadline_at or (utcnow() + timedelta(hours=1))
    spec = await specs.build(ctx.db, ctx.state, ctx.mission, job, session, deadline)
    return JSONResponse(spec.dump(), headers={"Cache-Control": "no-store"})


@router.operation("internalPostEvents")
async def post_events(ctx: JobCtxDep, id: str, body: m.EventBatch) -> m.JobControl:
    job, mission = ctx.job, ctx.mission
    secrets = _secrets(ctx)
    for event in body.events:
        if event.type in {
            "status",
            "plan",
            "checkpoint",
            "action_proposed",
            "action_status",
            "question",
            "question_answered",
            "data_class",
            "learning_signal",
            "objective_update",
            "judge",
        }:
            continue  # server-owned event types: the runner cannot forge them
        try:
            await events.append(
                ctx.db,
                mission.id,
                event.type,
                event.payload,
                job_id=job.id,
                agent=job.agent_name,
                step_id=job.step_id,
                ts=event.ts,
                secrets=secrets,
            )
        except events.EventValidationError as exc:
            raise Problem("validation", str(exc)) from exc
        if event.type == "usage":
            tin, tout = int(event.payload.get("tokens_in", 0)), int(event.payload.get("tokens_out", 0))
            job.tokens_in += tin
            job.tokens_out += tout
            mission.tokens_in += tin
            mission.tokens_out += tout
    limit = int(mission.budget.get("max_tokens") or 0)
    if limit and mission.tokens_in + mission.tokens_out >= limit and job.control_state == "run":
        job.control_state, job.control_reason = "cancel", "mission token budget exhausted"
    return await control_for(ctx.db, job)


@router.operation("internalJobControl")
async def job_control(request: Request, id: str, wait_s: int = 0) -> m.JobControl:
    """Long poll without holding the job row lock: short transactions, wake-ups from the event hub."""
    state: AppState = request.app.state.z
    token = token_hash(_bearer(request))
    deadline = asyncio.get_running_loop().time() + max(0, min(wait_s, MAX_WAIT_S))
    while True:
        async with state.sessions() as db, db.begin():
            job = (await db.execute(select(Job).where(Job.session_token_hash == token))).scalar_one_or_none()
            if job is None or job.id != id or JobStatus(job.status).terminal:
                raise Problem("unauthenticated", "invalid job token")
            job.lease_expires_at = utcnow() + timedelta(seconds=120)
            control = await control_for(db, job)
            if (
                control.state != "run"
                or control.messages
                or control.answers
                or asyncio.get_running_loop().time() >= deadline
            ):
                return control
        await state.hub.wait(job.mission_id, timeout=min(2.0, max(0.1, deadline - asyncio.get_running_loop().time())))


@router.operation("internalCheckpoint")
async def checkpoint(ctx: JobCtxDep, id: str, body: m.CheckpointIn) -> m.JobControl:
    blob = base64.b64decode(body.blob_b64, validate=True)
    if len(blob) > 64 * 1024 * 1024:
        raise Problem("validation", "checkpoint larger than 64 MiB")
    job = ctx.job
    job.checkpoint, job.checkpoint_step, job.steps_done = blob, body.step, max(job.steps_done, body.step)
    await events.append(
        ctx.db,
        job.mission_id,
        "checkpoint",
        {"step": body.step, "size": len(blob)},
        job_id=job.id,
        agent=job.agent_name,
        step_id=job.step_id,
    )
    return await control_for(ctx.db, job)


@router.operation("internalPostArtifact", status_code=201)
async def post_artifact(
    ctx: JobCtxDep,
    id: str,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()],
    name: Annotated[str, Form()],
) -> mm.Artifact:
    meta = m.ArtifactMeta.model_validate({"kind": kind, "name": name})
    data = await file.read(artifacts.MAX_ARTIFACT_BYTES + 1)
    try:
        row = await artifacts.create(
            ctx.db,
            ctx.state.settings.artifacts_dir,
            mission_id=ctx.mission.id,
            job_id=ctx.job.id,
            kind=meta.kind.value,
            name=meta.name,
            data=data,
            data_class=ctx.mission.data_class,
            content_type=file.content_type,
        )
    except artifacts.ArtifactTooLarge as exc:
        raise Problem("validation", str(exc), status=413) from exc
    await events.append(
        ctx.db,
        ctx.mission.id,
        "artifact",
        {"artifact_id": row.id, "kind": row.kind, "name": row.name},
        job_id=ctx.job.id,
        agent=ctx.job.agent_name,
        step_id=ctx.job.step_id,
    )
    return artifact_out(row)


def artifact_out(row: Artifact) -> mm.Artifact:
    return mm.Artifact(
        id=row.id,
        job_id=row.job_id,
        mission_id=row.mission_id,
        kind=row.kind,
        name=row.name,
        content_type=row.content_type,
        size=row.size,
        sha256=row.sha256,
        data_class=row.data_class,
        created_at=row.created_at,
    )


async def _runs_as(ctx: JobCtx, connector: Connector, scope: str) -> dict[str, Any]:
    if scope == "user":
        user = await ctx.db.get(User, ctx.mission.created_by)
        return {"scope": "user", "identity": user.username if user else "unknown", "user_id": ctx.mission.created_by}
    cred = (
        (
            await ctx.db.execute(
                select(Credential).where(
                    Credential.connector_id == connector.id,
                    Credential.purpose == "write",
                    Credential.scope == "service",
                )
            )
        )
        .scalars()
        .first()
    )
    return {
        "scope": "service",
        "identity": (cred.identity if cred and cred.identity else f"{connector.name} service account"),
    }


@router.operation("internalProposeAction", status_code=201)
async def propose_action(ctx: JobCtxDep, id: str, body: m.ActionIn) -> JSONResponse:
    job, mission = ctx.job, ctx.mission
    step = _step(mission, job.step_id)

    async def deny(rule: str, detail: str) -> JSONResponse:
        await events.append(
            ctx.db,
            mission.id,
            "denied",
            {"what": f"propose_action {body.operation}", "rule": rule, "detail": detail},
            job_id=job.id,
            agent=job.agent_name,
            step_id=job.step_id,
        )
        await audit.record(
            ctx.db,
            audit.Actor(f"agent:{job.agent_name}"),
            "action.denied",
            "operation",
            body.operation,
            result="denied",
            team=mission.team_id,
            after={"rule": rule, "detail": detail, "mission": mission.id},
        )
        from z_server.errors import problem_response

        return problem_response(Problem("forbidden", detail), ctx.request)

    if mission.autonomy != "propose":
        return await deny("read_only", "this mission is read-only: no writes can be proposed")
    if body.operation not in step.get("writes", []):
        return await deny("step_writes", f"{body.operation} is not in the writes of step {job.step_id}")
    op = await ctx.db.get(Operation, body.operation)
    connector = await ctx.db.get(Connector, op.connector_id) if op else None
    if op is None or connector is None or connector.name != body.connector or op.op_class == OperationClass.read.value:
        return await deny("unknown_write", f"{body.operation} is not a write operation of connector {body.connector}")
    if connector.review_status != "approved" or not op.exposed or not connector.enabled:
        return await deny("not_exposed", f"{body.operation} has not been exposed by an admin")
    try:
        jsonschema.validate(body.payload, op.input_schema or {"type": "object"})
        if body.operation in BUILTIN_PAYLOADS:
            BUILTIN_PAYLOADS[body.operation].model_validate(body.payload)
    except (jsonschema.ValidationError, ValueError) as exc:
        raise Problem(
            "validation", f"payload does not match {body.operation}: {getattr(exc, 'message', str(exc))[:500]}"
        ) from exc
    key = service.idempotency_key(mission.id, job.id, body.operation, body.payload)
    existing = (
        await ctx.db.execute(select(ProposedAction).where(ProposedAction.idempotency_key == key))
    ).scalar_one_or_none()
    if existing is not None:
        return JSONResponse((await action_out(ctx.db, existing)).dump(), status_code=201)
    artifact_path = None
    if body.operation == "gitlab.merge_requests.create":
        patch = await ctx.db.get(Artifact, str(body.payload.get("patch_artifact", "")))
        if patch is None or patch.mission_id != mission.id:
            raise Problem("validation", "patch_artifact must be an artifact of this mission")
        from pathlib import Path

        artifact_path = Path(patch.storage_path)
    destructive = op.op_class == OperationClass.destructive.value
    row = ProposedAction(
        id=new_id("act"),
        mission_id=mission.id,
        job_id=job.id,
        agent=job.agent_name,
        connector=connector.name,
        operation=op.id,
        op_class=op.op_class,
        summary=body.summary[:500],
        payload=body.payload,
        preview=build_preview(op.id, body.payload, artifact_path),
        reversible=not destructive,
        runs_as=await _runs_as(ctx, connector, step.get("credentials", "service")),
        status="pending",
        required_approvals=2 if destructive else 1,
        four_eyes=mission.four_eyes,
        idempotency_key=key,
    )
    ctx.db.add(row)
    await ctx.db.flush()
    await events.append(
        ctx.db,
        mission.id,
        "action_proposed",
        {
            "action_id": row.id,
            "connector": row.connector,
            "operation": row.operation,
            "class": row.op_class,
            "summary": row.summary,
        },
        job_id=job.id,
        agent=job.agent_name,
        step_id=job.step_id,
    )
    await audit.record(
        ctx.db,
        audit.Actor(f"agent:{job.agent_name}"),
        "action.proposed",
        "action",
        row.id,
        team=mission.team_id,
        runs_as=row.runs_as["identity"],
        after={"operation": row.operation, "class": row.op_class, "mission": mission.id},
    )
    return JSONResponse((await action_out(ctx.db, row)).dump(), status_code=201)


async def action_out(db: Any, row: ProposedAction) -> mm.ProposedAction:
    from z_server.db.models import ActionApproval

    approvals = (
        await db.execute(
            select(ActionApproval, User)
            .join(User, User.id == ActionApproval.user_id)
            .where(ActionApproval.action_id == row.id)
            .order_by(ActionApproval.at)
        )
    ).all()
    return mm.ProposedAction.model_validate(
        {
            "id": row.id,
            "job_id": row.job_id,
            "mission_id": row.mission_id,
            "agent": row.agent,
            "connector": row.connector,
            "operation": row.operation,
            "class": row.op_class,
            "summary": row.summary,
            "payload": row.payload,
            "preview": row.preview,
            "reversible": row.reversible,
            "judge_verdict": row.judge_verdict or None,
            "runs_as": row.runs_as,
            "status": row.status,
            "approvals": [
                {"by": u.username, "at": a.at, "step_up": a.step_up, "comment": a.comment} for a, u in approvals
            ],
            "required_approvals": row.required_approvals,
            "four_eyes": row.four_eyes,
            "result": row.result,
            "idempotency_key": row.idempotency_key,
            "superseded_by": row.superseded_by,
            "edited_from": row.edited_from,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


@router.operation("internalAskHuman", status_code=201)
async def ask_human(ctx: JobCtxDep, id: str, body: m.QuestionIn) -> mm.Question:
    job = ctx.job
    row = Question(
        id=new_id("q"),
        mission_id=job.mission_id,
        job_id=job.id,
        agent=job.agent_name,
        text=body.text,
        options=list(body.options or []),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    await events.append(
        ctx.db,
        job.mission_id,
        "question",
        {"question_id": row.id, "text": row.text, "options": row.options},
        job_id=job.id,
        agent=job.agent_name,
        step_id=job.step_id,
    )
    await service.set_job_status(ctx.db, job, JobStatus.needs_attention, "question")
    await service.advance(ctx.db, ctx.state, ctx.mission)
    return question_out(row)


def question_out(row: Question) -> mm.Question:
    return mm.Question(
        id=row.id,
        job_id=row.job_id,
        mission_id=row.mission_id,
        agent=row.agent,
        text=row.text,
        options=list(row.options),
        answer=row.answer,
        answered_by=row.answered_by,
        answered_at=row.answered_at,
        created_at=row.created_at,
    )


@router.operation("internalSearchMemory")
async def search_memory(ctx: JobCtxDep, id: str, body: m.MemorySearch) -> list[m.MemorySnippet]:
    profile = await service.agent_profile(ctx.db, ctx.job)
    config = profile.get("memory") or {}
    top_k = min(body.top_k or int(config.get("top_k", 8)), 50)
    try:
        facts = await memory.search(
            ctx.db,
            ctx.state.http,
            ctx.state.settings.litellm_url,
            service.mission_key(ctx.state, ctx.mission),
            query=body.query,
            scopes=list(config.get("scopes", ["org", "team", "agent"])),
            team=ctx.mission.team_id,
            agent=ctx.job.agent_name,
            top_k=top_k,
        )
    except memory.MemoryUnavailable as exc:
        raise Problem("unavailable", str(exc)) from exc
    return [m.MemorySnippet(id=f.id, scope=f.scope, text=f.text) for f in facts]


@router.operation("internalSpawnSubagent", status_code=201)
async def spawn_subagent(ctx: JobCtxDep, id: str, body: m.SubagentIn) -> m.SubagentStatus:
    parent = ctx.job
    profile = await service.agent_profile(ctx.db, parent)
    if "spawn_subagent" not in profile.get("tools", []):
        raise Problem("forbidden", "this agent cannot spawn sub-agents")
    agent = (
        await ctx.db.execute(select(Agent).where(Agent.name == body.agent, Agent.status != "disabled"))
    ).scalar_one_or_none()
    if agent is None:
        raise Problem("validation", f"unknown agent {body.agent}")
    child = Job(
        id=new_id("job"),
        mission_id=parent.mission_id,
        step_id=parent.step_id,
        agent_id=agent.id,
        agent_name=agent.name,
        agent_version=agent.version,
        parent_job_id=parent.id,
        input={**(body.input or {}), "inputs": ctx.mission.inputs},
        prompt=body.prompt,
        status=JobStatus.queued.value,
        model=agent.profile["model"],
        deadline_at=parent.deadline_at,
    )
    ctx.db.add(child)
    await ctx.db.flush()
    await events.append(
        ctx.db,
        parent.mission_id,
        "subagent",
        {"child_job_id": child.id, "agent": agent.name, "input_summary": body.prompt[:200]},
        job_id=parent.id,
        agent=parent.agent_name,
        step_id=parent.step_id,
    )
    return m.SubagentStatus(child_job_id=child.id, status=JobStatus.queued)


@router.operation("internalSubagentStatus")
async def subagent_status(ctx: JobCtxDep, id: str, child_id: str) -> m.SubagentStatus:
    child = await ctx.db.get(Job, child_id)
    if child is None or child.parent_job_id != ctx.job.id:
        raise Problem("not-found")
    error = (child.error or {}).get("message") if child.error else None
    return m.SubagentStatus(child_job_id=child.id, status=JobStatus(child.status), output=child.output, error=error)


@router.operation("internalReportObjective")
async def report_objective(ctx: JobCtxDep, id: str, body: m.ObjectiveReport) -> JSONResponse:
    raise Problem("conflict", "objectives are not enabled in this version (v1.1)")


@router.operation("internalCompleteJob")
async def complete_job(ctx: JobCtxDep, id: str, body: m.JobComplete) -> mm.Job:
    job, mission = ctx.job, ctx.mission
    status = JobStatus(body.status)
    reason = body.needs_attention_reason.value if body.needs_attention_reason else None
    step = _step(mission, job.step_id)
    if status == JobStatus.succeeded and step.get("output"):
        spec_schema = await specs.output_spec(ctx.db, ctx.state, mission, step)
        problems = (
            []
            if spec_schema is None
            else [
                e.message for e in jsonschema.Draft202012Validator(spec_schema.schema_).iter_errors(body.output or {})
            ]
        )
        if body.output is None or problems:
            status, reason = JobStatus.needs_attention, "invalid_output"
            await events.append(
                ctx.db,
                mission.id,
                "validation_error",
                {
                    "where": "output",
                    "errors": [{"path": "output", "message": p} for p in (problems or ["missing output"])][:20],
                },
                job_id=job.id,
                agent=job.agent_name,
                step_id=job.step_id,
            )
    job.output = body.output if status == JobStatus.succeeded else job.output
    job.steps_done = max(job.steps_done, body.steps)
    if body.error:
        job.error = {"class": body.error.class_, "message": body.error.message[:2000]}
    elif status == JobStatus.succeeded:
        job.error = None  # a retried job that finally succeeded keeps no stale error
    await service.set_job_status(ctx.db, job, status, reason)
    await service.advance(ctx.db, ctx.state, mission)
    return job_out(job)


def job_out(job: Job) -> mm.Job:
    return mm.Job.model_validate(
        {
            "id": job.id,
            "mission_id": job.mission_id,
            "step_id": job.step_id,
            "agent": job.agent_name,
            "agent_version": job.agent_version,
            "parent_job_id": job.parent_job_id,
            "item_key": job.item_key,
            "input": job.input,
            "status": job.status,
            "needs_attention_reason": job.needs_attention_reason,
            "attempt": job.attempt,
            "resume_from_step": job.resume_from_step,
            "steps_done": job.steps_done,
            "model": job.model,
            "tokens_in": job.tokens_in,
            "tokens_out": job.tokens_out,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "ended_at": job.ended_at,
            "error": job.error,
            "output": job.output,
        }
    )


async def _staged(ctx: JobCtx) -> workspace.Staged:
    staged = await workspace.stage(ctx.db, ctx.mission, ctx.job)
    job = ctx.job
    for f in staged.files:
        if f.verdict.verdict != JudgeVerdict.clean:
            await events.append(
                ctx.db,
                job.mission_id,
                "judge",
                {
                    "subject": "staged_input",
                    "ref": f.path,
                    "verdict": f.verdict.verdict.value,
                    "categories": f.verdict.categories,
                    "reason": f.verdict.reason,
                },
                job_id=job.id,
                agent=job.agent_name,
                step_id=job.step_id,
            )
    if staged.blocked and job.status != JobStatus.needs_attention.value:
        job.control_state, job.control_reason = (
            "pause",
            "the judge withheld staged inputs: " + ", ".join(f.path for f in staged.blocked)[:400],
        )
        await service.set_job_status(ctx.db, job, JobStatus.needs_attention, "judge_blocked")
        await service.advance(ctx.db, ctx.state, ctx.mission)
    return staged


@router.operation("internalJobWorkspace")
async def job_workspace(ctx: JobCtxDep, id: str) -> Response:
    staged = await _staged(ctx)
    return Response(staged.archive(), media_type="application/gzip", headers={"Cache-Control": "no-store"})


@router.operation("internalJobWorkspaceManifest")
async def job_workspace_manifest(ctx: JobCtxDep, id: str) -> m.WorkspaceManifest:
    return (await _staged(ctx)).manifest()


@router.operation("internalRaiseDataClass", status_code=204)
async def raise_data_class(request: Request, id: str, body: m.DataClassRaise) -> Response:
    """Invariant 10: the class only goes up, and the mission key allowlist shrinks before anything else moves."""
    state = await require_service(request)
    async with state.sessions() as db, db.begin():
        job = await db.get(Job, id)
        mission = await db.get(Mission, job.mission_id, with_for_update=True) if job else None
        if job is None or mission is None:
            raise Problem("not-found")
        try:
            await service.raise_data_class(db, state, mission, body.to, body.cause, body.detail, job)
        except LlmKeyError as exc:
            raise Problem(
                "unavailable", "could not restrict the mission model key; the result must not be delivered"
            ) from exc
    return Response(status_code=204)
