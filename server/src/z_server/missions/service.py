"""Mission lifecycle (docs/02 §2, docs/03 §2, docs/04 §2).

The jobs table is authoritative; `missions.plan` caches step statuses and job counts for the UI.
`advance()` is idempotent: it recomputes step statuses from jobs, creates jobs for steps that became ready
(fan-out expanded by the scheduler side, not the orchestrator), skips steps whose `when` is false, and derives
the mission status. Call it after any job or action change, inside the transaction that made the change.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jsonschema
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import TERMINAL_JOB, Autonomy, DataClass, JobStatus, MissionStatus, StepStatus
from z_common.models.manifests import MissionPlan, PlanStep, StepJobs

from z_server import audit, events
from z_server.db.models import Agent, AgentVersion, Job, Mission, Playbook, ProposedAction, Question, Team
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.llm.litellm import LlmKeyError
from z_server.llm.routing import allowed_aliases
from z_server.missions.expressions import ExpressionError, truthy
from z_server.missions.templates import TemplateFailure, render, render_value
from z_server.rbac import Principal
from z_server.state import AppState

log = logging.getLogger("z.missions")
ACTIVE_ACTION = ("pending", "approved", "applying")
DEFAULT_TIMEOUT_S = 7200
STEP_REF_OUTPUTS = ".outputs"
FAILED_JOB = {JobStatus.failed.value, JobStatus.budget_exceeded.value, JobStatus.timed_out.value}


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- creation


def apply_defaults(schema: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    out = dict(inputs)
    for name, prop in (schema.get("properties") or {}).items():
        if name not in out and isinstance(prop, dict) and "default" in prop:
            out[name] = prop["default"]
    return out


def validate_inputs(schema: dict[str, Any] | None, inputs: dict[str, Any]) -> dict[str, Any]:
    if not schema:
        return inputs
    filled = apply_defaults(schema, inputs)
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(filled), key=lambda e: list(e.path))
    if errors:
        raise Problem(
            "validation",
            "inputs do not match the playbook",
            errors=[{"path": "/".join(str(p) for p in e.path) or "inputs", "message": e.message} for e in errors],
        )
    return filled


def plan_from_playbook(manifest: dict[str, Any]) -> MissionPlan:
    steps = [PlanStep.model_validate({**s, "status": "pending"}) for s in manifest["steps"]]
    writes = sorted({w for s in manifest["steps"] for w in s.get("writes", [])})
    return MissionPlan(playbook=manifest["name"], steps=steps, writes=writes)


@dataclass
class MissionRequest:
    title: str | None
    prompt: str | None
    playbook: Playbook | None
    inputs: dict[str, Any]
    plan: MissionPlan | None
    connectors: list[str]
    data_class: DataClass | None
    autonomy: Autonomy | None
    plan_first: bool
    budget: dict[str, Any]
    session_id: str | None
    trigger_id: str | None = None


async def _strictest_autonomy(db: AsyncSession, plan: MissionPlan, *requested: Autonomy | None) -> Autonomy:
    names = {s.agent for s in plan.steps}
    agents = (await db.execute(select(Agent).where(Agent.name.in_(names)))).scalars()
    return Autonomy.strictest(*requested, *(Autonomy(a.profile.get("autonomy", "propose")) for a in agents))


async def create(db: AsyncSession, state: AppState, user: Principal, request: MissionRequest) -> Mission:
    manifest = request.playbook.manifest if request.playbook else None
    if manifest is not None:
        inputs = validate_inputs((manifest.get("inputs") or {}).get("schema"), request.inputs)
        plan = plan_from_playbook(manifest)
    elif request.plan is not None:
        inputs, plan = request.inputs, request.plan
        for step in plan.steps:
            if step.status is None:
                step.status = StepStatus.pending
    else:
        raise Problem(
            "validation", "a free-form mission needs a plan (draft one with POST /missions/draft-plan) or an @agent"
        )
    await _check_plan_agents(db, plan)
    from z_server.llm import policy

    config = state.config
    default_class = DataClass((await policy.effective(db, config)).get("default_mission_class", "C2"))
    data_class = request.data_class or (
        DataClass(manifest["data_class"]) if manifest and manifest.get("data_class") else default_class
    )
    autonomy = await _strictest_autonomy(
        db, plan, request.autonomy, Autonomy(manifest["autonomy"]) if manifest and manifest.get("autonomy") else None
    )
    budget = {
        "max_tokens": int(
            request.budget.get("max_tokens")
            or (manifest or {}).get("budget", {}).get("max_tokens")
            or config.get("budgets", {}).get("default_mission_tokens", 10_000_000)
        ),
        "timeout_s": int(
            request.budget.get("timeout_s") or (manifest or {}).get("budget", {}).get("timeout_s") or DEFAULT_TIMEOUT_S
        ),
    }
    title = request.title or (manifest["description"] if manifest else (request.prompt or "Mission"))[:200]
    team = await _starter_team(db, user)
    mission = Mission(
        id=new_id("msn"),
        title=title,
        prompt=request.prompt,
        playbook_id=request.playbook.id if request.playbook else None,
        playbook_name=request.playbook.name if request.playbook else None,
        playbook_version=request.playbook.version if request.playbook else None,
        plan=plan.dump(),
        inputs=inputs,
        connectors=sorted(set(request.connectors or (manifest or {}).get("connectors", []))),
        data_class=data_class.value,
        autonomy=autonomy.value,
        plan_first=request.plan_first,
        four_eyes=bool((manifest or {}).get("four_eyes", False)),
        status=MissionStatus.planned.value if request.plan_first else MissionStatus.draft.value,
        created_by=user.user_id,
        team_id=team,
        session_id=request.session_id,
        trigger_id=request.trigger_id,
        budget=budget,
    )
    db.add(mission)
    await db.flush()
    await events.append(db, mission.id, "status", {"entity": "mission", "from": None, "to": mission.status})
    await events.append(db, mission.id, "plan", {"steps": [s.dump() for s in plan.steps], "writes": plan.writes})
    if request.playbook is not None:
        await db.execute(
            update(Playbook)
            .where(Playbook.id == request.playbook.id)
            .values(usage_count=Playbook.usage_count + 1, last_run_at=utcnow())
        )
    return mission


async def _check_plan_agents(db: AsyncSession, plan: MissionPlan) -> None:
    names = {s.agent for s in plan.steps}
    found = {
        a
        for (a,) in (
            await db.execute(select(Agent.name).where(Agent.name.in_(names), Agent.status != "disabled"))
        ).all()
    }
    missing = sorted(names - found)
    if missing:
        raise Problem("validation", f"unknown or disabled agents in the plan: {', '.join(missing)}")


async def _starter_team(db: AsyncSession, user: Principal) -> str | None:
    teams = sorted(user.teams)
    if len(teams) > 1:
        names = {t.id: t.name for t in (await db.execute(select(Team).where(Team.id.in_(teams)))).scalars()}
        teams.sort(key=lambda t: names.get(t, t))
    return teams[0] if teams else None


# ---------------------------------------------------------------- start, control


async def start(db: AsyncSession, state: AppState, mission: Mission) -> None:
    if mission.status not in {MissionStatus.draft.value, MissionStatus.planned.value}:
        raise Problem("conflict", f"mission is {mission.status}")
    from z_server import usage

    await usage.check_start(
        db, mission, utcnow(), int(state.config.get("budgets", {}).get("org_monthly_tokens") or 0) or None
    )
    await _issue_key(db, state, mission)
    mission.started_at = utcnow()
    await set_status(db, mission, MissionStatus.queued)
    await advance(db, state, mission)


async def _issue_key(db: AsyncSession, state: AppState, mission: Mission) -> None:
    from z_server.llm.inference import routing_config

    aliases = allowed_aliases(await routing_config(db, state.config), DataClass(mission.data_class))
    if not aliases:
        raise Problem(
            "unavailable",
            f"no enabled model may receive {mission.data_class} data: check providers and the routing policy",
        )
    try:
        key = await state.llm_keys.create(
            alias=f"mission-{mission.id}",
            models=aliases,
            metadata={
                "mission": mission.id,
                "data_class": mission.data_class,
                "team": mission.team_id,
                "playbook": mission.playbook_name,
            },
            duration_s=int(mission.budget.get("timeout_s", DEFAULT_TIMEOUT_S)) + 3600,
        )
    except LlmKeyError as exc:
        raise Problem("unavailable", f"the model gateway could not create the mission key: {exc}") from exc
    mission.llm_key_alias = key.alias
    mission.llm_key_enc = state.sealer.seal(key.key, f"llm-key:{mission.id}")
    mission.deadline_at = utcnow() + timedelta(seconds=int(mission.budget.get("timeout_s", DEFAULT_TIMEOUT_S)))


async def check_plan(db: AsyncSession, plan: MissionPlan) -> None:
    await _check_plan_agents(db, plan)


async def pause(db: AsyncSession, mission: Mission, reason: str) -> None:
    """Queued jobs wait (the queue only claims active missions); running jobs get a pause control."""
    await set_status(db, mission, MissionStatus.paused, reason)
    for job in (
        await db.execute(select(Job).where(Job.mission_id == mission.id, Job.status == JobStatus.running.value))
    ).scalars():
        job.control_state, job.control_reason = "pause", reason
        await set_job_status(db, job, JobStatus.paused, reason)


async def has_paused_jobs(db: AsyncSession, mission: Mission) -> bool:
    found = (
        await db.execute(
            select(Job.id).where(
                Job.mission_id == mission.id,
                Job.control_state == "pause",
                Job.status.notin_([s.value for s in TERMINAL_JOB]),
            )
        )
    ).first()
    return found is not None


async def resume(db: AsyncSession, state: AppState, mission: Mission) -> None:
    """Also releases jobs a guardrail or the judge paused, once a human has looked at them."""
    for job in (
        await db.execute(
            select(Job).where(
                Job.mission_id == mission.id,
                Job.control_state == "pause",
                Job.status.notin_([s.value for s in TERMINAL_JOB]),
            )
        )
    ).scalars():
        job.control_state, job.control_reason = "run", None
        if job.status in {JobStatus.paused.value, JobStatus.needs_attention.value}:
            await set_job_status(db, job, JobStatus.running, "resumed")
    if mission.status == MissionStatus.paused.value:
        await set_status(db, mission, MissionStatus.running, "resumed")
    await advance(db, state, mission)


REOPENABLE = {MissionStatus.failed.value, MissionStatus.timed_out.value}


async def retry_job(db: AsyncSession, state: AppState, mission: Mission, job: Job) -> None:
    """A new attempt from the last checkpoint. A mission that failed because of this job is reopened with a new
    model key (the old one was deleted when it ended)."""
    if MissionStatus(mission.status).terminal:
        if mission.status not in REOPENABLE:
            raise Problem("conflict", f"mission is {mission.status}")
        await _issue_key(db, state, mission)
        mission.ended_at = None
        await set_status(db, mission, MissionStatus.queued, "retry")
    previous = job.status
    job.attempt += 1
    job.max_attempts = max(job.max_attempts, job.attempt)
    job.resume_from_step = job.checkpoint_step
    job.error, job.output, job.ended_at, job.started_at = None, None, None, None
    job.control_state, job.control_reason = "run", None
    job.lease_owner, job.lease_expires_at, job.available_at = None, None, utcnow()
    limits = (await agent_profile(db, job))["limits"]
    timeout = timedelta(seconds=int(limits["timeout_s"]))
    job.deadline_at = min(mission.deadline_at or utcnow() + timeout, utcnow() + timeout)
    await set_job_status(db, job, JobStatus.queued, f"retry of a {previous} job")
    await advance(db, state, mission)


async def raise_data_class(
    db: AsyncSession,
    state: AppState,
    mission: Mission,
    to: DataClass,
    cause: str,
    detail: str | None = None,
    job: Job | None = None,
    actor: str = "service:gateway",
) -> bool:
    """Invariant 10: the class only goes up, and the mission key allowlist shrinks before anything else moves.
    Raises LlmKeyError when the key cannot be restricted (the caller must then withhold the result)."""
    current = DataClass(mission.data_class)
    if to.rank <= current.rank:
        return False
    if mission.llm_key_enc:
        from z_server.llm.inference import routing_config

        await state.llm_keys.set_models(
            mission_key(state, mission), allowed_aliases(await routing_config(db, state.config), to)
        )
    mission.data_class = to.value
    await events.append(
        db,
        mission.id,
        "data_class",
        {"from": current.value, "to": to.value, "cause": cause, "detail": detail},
        job_id=job.id if job else None,
        agent=job.agent_name if job else None,
        step_id=job.step_id if job else None,
    )
    await audit.record(
        db,
        audit.Actor(actor),
        "mission.data_class_raised",
        "mission",
        mission.id,
        team=mission.team_id,
        before={"data_class": current.value},
        after={"data_class": to.value, "cause": cause},
    )
    return True


def mission_key(state: AppState, mission: Mission) -> str:
    if not mission.llm_key_enc:
        raise Problem("conflict", "mission has no model key")
    return state.sealer.open(mission.llm_key_enc, f"llm-key:{mission.id}")


async def set_status(
    db: AsyncSession, mission: Mission, status: MissionStatus, reason: str | None = None, waiting: str | None = None
) -> None:
    if mission.status == status.value and mission.waiting_reason == waiting:
        return
    previous = mission.status
    mission.status, mission.waiting_reason = status.value, waiting
    if status.terminal:
        mission.ended_at = utcnow()
    await events.append(
        db,
        mission.id,
        "status",
        {"entity": "mission", "from": previous, "to": status.value, "reason": reason or waiting},
    )


async def set_job_status(db: AsyncSession, job: Job, status: JobStatus, reason: str | None = None) -> None:
    if job.status == status.value and job.needs_attention_reason == (
        reason if status == JobStatus.needs_attention else None
    ):
        return
    previous = job.status
    job.status = status.value
    job.needs_attention_reason = reason if status == JobStatus.needs_attention else None
    if status.terminal:
        job.ended_at = utcnow()
        job.session_token_hash = None
        job.bootstrap_token_hash = None
    await events.append(
        db,
        job.mission_id,
        "status",
        {"entity": "job", "id": job.id, "from": previous, "to": status.value, "reason": reason},
        job_id=job.id,
        agent=job.agent_name,
        step_id=job.step_id,
    )


async def stop_jobs(db: AsyncSession, mission: Mission, final: JobStatus, reason: str) -> None:
    """Queued jobs end at once; running ones get a cancel control and end when the runner reports or the
    watchdog sees them stop."""
    for job in (
        await db.execute(
            select(Job).where(Job.mission_id == mission.id, Job.status.notin_([s.value for s in TERMINAL_JOB]))
        )
    ).scalars():
        if job.status in {JobStatus.queued.value, JobStatus.paused.value, JobStatus.needs_attention.value}:
            await set_job_status(db, job, final, reason)
        else:
            job.control_state, job.control_reason = "cancel", reason


async def finish(
    db: AsyncSession, state: AppState, mission: Mission, status: MissionStatus, reason: str | None = None
) -> None:
    await stop_jobs(
        db,
        mission,
        JobStatus.cancelled if status != MissionStatus.budget_exceeded else JobStatus.budget_exceeded,
        reason or status.value,
    )
    await set_status(db, mission, status, reason)
    if mission.llm_key_enc:
        try:
            await state.llm_keys.delete(mission_key(state, mission))
        except LlmKeyError:
            log.warning("could not delete the key of mission %s; it expires with its duration", mission.id)
        finally:
            mission.llm_key_enc = None
    await audit.record(
        db,
        audit.Actor("system"),
        f"mission.{status.value}",
        "mission",
        mission.id,
        team=mission.team_id,
        after={"reason": reason, "tokens_in": mission.tokens_in, "tokens_out": mission.tokens_out},
    )


# ---------------------------------------------------------------- advancement


def _item_key(item: Any, index: int, source_key: str | None) -> str:
    if source_key:
        return source_key
    return str(index)


def _lookup(path: str, context: dict[str, Any]) -> Any:
    """Resolve `steps.<id>.output.a.b` or `steps.<id>.outputs` inside the context."""
    parts = path.split(".")
    value: Any = context
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            raise Problem("conflict", f"{path}: '{part}' is missing")
        value = value[part]
    return value


@dataclass
class StepState:
    jobs: list[Job]

    @property
    def outputs(self) -> list[Any]:
        return [
            j.output
            for j in sorted(self.jobs, key=lambda j: j.item_index or 0)
            if j.status == JobStatus.succeeded.value
        ]

    def counts(self) -> StepJobs:
        c: dict[str, int] = defaultdict(int)
        for job in self.jobs:
            c[job.status] += 1
        return StepJobs(
            total=len(self.jobs),
            queued=c["queued"],
            running=c["running"] + c["paused"],
            succeeded=c["succeeded"],
            failed=c["failed"] + c["budget_exceeded"] + c["timed_out"] + c["cancelled"],
            needs_attention=c["needs_attention"],
        )


def step_status(step: PlanStep, state: StepState | None) -> str:
    if step.status in {"skipped"}:
        return "skipped"
    if not state or not state.jobs:
        return "succeeded" if step.status == "succeeded" else "pending"
    statuses = {j.status for j in state.jobs}
    if statuses & {"queued", "running", "paused"}:
        return "running"
    if "needs_attention" in statuses:
        return "waiting"
    if statuses & FAILED_JOB:
        return "failed"
    if "cancelled" in statuses:
        return "cancelled"
    return "succeeded"


async def advance(db: AsyncSession, state: AppState, mission: Mission) -> None:
    mission = await db.get(Mission, mission.id, with_for_update=True) or mission
    if MissionStatus(mission.status).terminal or mission.status in {
        MissionStatus.draft.value,
        MissionStatus.planned.value,
    }:
        return
    plan = MissionPlan.model_validate(mission.plan)
    jobs = (
        (await db.execute(select(Job).where(Job.mission_id == mission.id, Job.parent_job_id.is_(None)))).scalars().all()
    )
    by_step: dict[str, StepState] = defaultdict(lambda: StepState([]))
    for job in jobs:
        by_step[job.step_id].jobs.append(job)
    context = {"inputs": mission.inputs, "steps": {}}
    statuses: dict[str, str] = {}
    paused = mission.status == MissionStatus.paused.value
    for step in plan.steps:
        st = by_step.get(step.id)
        status = step_status(step, st)
        if (
            status == "pending"
            and not paused
            and all(statuses.get(d) in {"succeeded", "skipped"} for d in step.depends_on)
        ):
            status = await _schedule_step(db, state, mission, step, context, by_step)
        statuses[step.id] = status
        step.status = StepStatus(status)
        step.jobs = by_step[step.id].counts() if step.id in by_step else StepJobs()
        outputs = by_step[step.id].outputs if step.id in by_step else []
        context["steps"][step.id] = {
            "outputs": outputs,
            "output": outputs[0]
            if len(outputs) == 1 and not step.fanout
            else (outputs[-1] if outputs and not step.fanout else None),
        }
    new_plan = plan.dump()
    if new_plan != mission.plan:
        mission.plan = new_plan
        await events.append(db, mission.id, "plan", {"steps": [s.dump() for s in plan.steps], "writes": plan.writes})
    await _derive_status(db, state, mission, statuses)


async def _schedule_step(
    db: AsyncSession,
    state: AppState,
    mission: Mission,
    step: PlanStep,
    context: dict[str, Any],
    by_step: dict[str, StepState],
) -> str:
    try:
        if step.when and not truthy(step.when, context):
            await events.append(
                db,
                mission.id,
                "status",
                {"entity": "step", "id": step.id, "from": "pending", "to": "skipped", "reason": f"when: {step.when}"},
                step_id=step.id,
            )
            return "skipped"
        items: list[tuple[Any, str | None]]
        if step.fanout:
            source = step.fanout.over
            if source.endswith(STEP_REF_OUTPUTS):
                src_id = source.split(".")[1]
                src_jobs = sorted(
                    (j for j in by_step[src_id].jobs if j.status == JobStatus.succeeded.value),
                    key=lambda j: j.item_index or 0,
                )
                items = [(j.output, j.item_key) for j in src_jobs]
            else:
                value = _lookup(source, context)
                if not isinstance(value, list):
                    raise Problem("conflict", f"{source} is not a list")
                items = [(v, None) for v in value]
            if step.fanout.when:
                step_outputs = {
                    sid: {j.item_key: j.output for j in s.jobs if j.status == JobStatus.succeeded.value}
                    for sid, s in by_step.items()
                }

                def step_for(name: str, item: Any) -> Any:
                    return step_outputs.get(name, {}).get(item.get("_key") if isinstance(item, dict) else None)

                kept = []
                for value, key in items:
                    probe = {**(value if isinstance(value, dict) else {"value": value}), "_key": key}
                    if truthy(step.fanout.when, {**context, "item": probe}, step_for):
                        kept.append((value, key))
                items = kept
        else:
            items = [(None, None)]
        created = 0
        for index, (item, source_key) in enumerate(items):
            await _create_job(
                db,
                state,
                mission,
                step,
                context,
                item if step.fanout else None,
                index if step.fanout else None,
                _item_key(item, index, source_key) if step.fanout else None,
            )
            created += 1
        if created == 0:
            await events.append(
                db,
                mission.id,
                "status",
                {"entity": "step", "id": step.id, "from": "pending", "to": "succeeded", "reason": "no items"},
                step_id=step.id,
            )
            return "succeeded"
        return "running"
    except (ExpressionError, TemplateFailure, Problem) as exc:
        detail = exc.detail if isinstance(exc, Problem) else str(exc)
        await events.append(
            db,
            mission.id,
            "status",
            {"entity": "step", "id": step.id, "from": "pending", "to": "failed", "reason": detail},
            step_id=step.id,
        )
        return "failed"


async def _create_job(
    db: AsyncSession,
    state: AppState,
    mission: Mission,
    step: PlanStep,
    context: dict[str, Any],
    item: Any,
    index: int | None,
    key: str | None,
) -> Job:
    agent = (await db.execute(select(Agent).where(Agent.name == step.agent))).scalar_one()
    scope = {**context, "item": item if item is not None else {}}
    prompt = render(step.effective_prompt, scope)
    job_input: dict[str, Any] = {"inputs": mission.inputs}
    if item is not None:
        job_input["item"] = item
    if step.inputs_from:
        job_input["from"] = _lookup(step.inputs_from, context)
    if step.repo:
        job_input["repo"] = render_value(step.repo, scope)
    limits = agent.profile["limits"]
    timeout = timedelta(seconds=int(limits["timeout_s"]))
    deadline = min(mission.deadline_at or utcnow() + timeout, utcnow() + timeout)
    job = Job(
        id=new_id("job"),
        mission_id=mission.id,
        step_id=step.id,
        agent_id=agent.id,
        agent_name=agent.name,
        agent_version=agent.version,
        item_key=key,
        item_index=index,
        input=job_input,
        prompt=prompt,
        status=JobStatus.queued.value,
        model=step.model or agent.profile["model"],
        deadline_at=deadline,
    )
    db.add(job)
    await db.flush()
    await events.append(
        db,
        mission.id,
        "status",
        {"entity": "job", "id": job.id, "from": None, "to": "queued"},
        job_id=job.id,
        agent=agent.name,
        step_id=step.id,
    )
    return job


async def _derive_status(db: AsyncSession, state: AppState, mission: Mission, statuses: dict[str, str]) -> None:
    if mission.status == MissionStatus.paused.value:
        return
    values = set(statuses.values())
    if mission.tokens_in + mission.tokens_out >= int(mission.budget.get("max_tokens", 0) or 1 << 62):
        await finish(db, state, mission, MissionStatus.budget_exceeded, "mission token budget exhausted")
        return
    if "failed" in values or "cancelled" in values:
        await finish(db, state, mission, MissionStatus.failed, "a step failed")
        return
    pending_actions = (
        await db.execute(
            select(func.count())
            .select_from(ProposedAction)
            .where(ProposedAction.mission_id == mission.id, ProposedAction.status.in_(ACTIVE_ACTION))
        )
    ).scalar_one()
    open_questions = (
        await db.execute(
            select(func.count())
            .select_from(Question)
            .where(Question.mission_id == mission.id, Question.answer.is_(None))
        )
    ).scalar_one()
    if values <= {"succeeded", "skipped"}:
        if pending_actions:
            await set_status(db, mission, MissionStatus.waiting, waiting="approval")
        else:
            await finish(db, state, mission, MissionStatus.succeeded)
        return
    if "waiting" in values and "running" not in values:
        reason = "question" if open_questions else ("approval" if pending_actions else None)
        await set_status(db, mission, MissionStatus.waiting, waiting=reason)
        return
    if "running" in values:
        running = (
            await db.execute(
                select(func.count())
                .select_from(Job)
                .where(Job.mission_id == mission.id, Job.status.in_(["running", "paused"]))
            )
        ).scalar_one()
        await set_status(db, mission, MissionStatus.running if running else MissionStatus.queued)
        return
    await set_status(db, mission, MissionStatus.queued)


def idempotency_key(mission_id: str, job_id: str, operation: str, payload: dict[str, Any]) -> str:
    blob = json.dumps([mission_id, job_id, operation, payload], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


async def agent_profile(db: AsyncSession, job: Job) -> dict[str, Any]:
    version = (
        await db.execute(
            select(AgentVersion).where(AgentVersion.agent_id == job.agent_id, AgentVersion.version == job.agent_version)
        )
    ).scalar_one_or_none()
    if version is not None:
        return dict(version.profile)
    agent = await db.get(Agent, job.agent_id)
    if agent is None:
        raise Problem("not-found", "agent of the job disappeared")
    return dict(agent.profile)
