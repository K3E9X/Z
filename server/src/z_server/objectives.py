"""Objectives: agents that look after something (docs/04 §3, docs/03 §4 Objective).

On its cadence an objective is measured, its status recorded (`on_track` / `at_risk` / `off_track`), and when
there is a gap Z suggests missions from the objective's allowed playbooks (or a free-form mission for the
agent) in Review. With `mode: auto_start_read_only` a suggestion whose plan has no writes is started at once,
as the objective's owner, if it fits the objective's budget for the period. Anything that writes is only
suggested; its writes still go through approval (invariant 4).

Metrics
- `z`: open_proposals (pending approvals of the agent), mission_failures (failed missions with the agent over
  `window_hours`, default 168), untriaged_findings (incoming tasks older than `older_than_hours`, default 48,
  optionally of one `source`), stale_tickets (Jira issues not done and not updated for `days`, default 30)
  and overdue_tickets (Jira issues not done and past their due date) over the agent's `jira_project`
  assignments or `params.project`.
- `connector`: a read operation called with the read credential, then `path` (dotted) counted or summed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import ObjectiveMode, ObjectiveStatus
from z_common.models.manifests import EventCadence, ObjectiveManifest

from z_server import audit
from z_server.auth import identity
from z_server.db.models import (
    Agent,
    Assignment,
    Connector,
    Mission,
    MissionSuggestion,
    Objective,
    ObjectiveCheck,
    Operation,
    Playbook,
    ProposedAction,
    Task,
    User,
)
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.intake.cron import Cron, CronError
from z_server.missions import service
from z_server.state import AppState

log = logging.getLogger("z.objectives")
JIRA_PAGE = 50
JIRA_MAX = 1000  # counts stop here; the explanation says so
AT_RISK_SHARE = 0.2
PERIODS = {"day": timedelta(days=1), "week": timedelta(weeks=1), "month": timedelta(days=30)}


class MeasureError(Exception):
    pass


@dataclass
class Measure:
    value: float
    note: str = ""


def now() -> datetime:
    return datetime.now(UTC)


def status_for(value: float, target: float, direction: str) -> ObjectiveStatus:
    gap = value - target if direction == "down" else target - value
    if gap <= 0:
        return ObjectiveStatus.on_track
    if target and gap <= abs(target) * AT_RISK_SHARE:
        return ObjectiveStatus.at_risk
    return ObjectiveStatus.off_track


def next_check(manifest: ObjectiveManifest, after: datetime) -> datetime | None:
    if isinstance(manifest.cadence, EventCadence):
        return None
    return Cron(manifest.cadence, "UTC").next_after(after)


def validate_cadence(manifest: ObjectiveManifest) -> None:
    if not isinstance(manifest.cadence, EventCadence):
        try:
            Cron(manifest.cadence, "UTC")
        except CronError as exc:
            raise Problem("validation", f"cadence: {exc}") from exc


# ---------------------------------------------------------------- measuring


def pick(body: Any, path: str | None) -> Any:
    value = body
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def aggregate(value: Any, how: str) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, list):
        if how == "sum":
            return float(sum(v for v in value if isinstance(v, int | float) and not isinstance(v, bool)))
        return float(len(value))
    if isinstance(value, dict):
        return float(len(value))
    raise MeasureError("the metric path does not point to a number or a list")


async def _read_op(db: AsyncSession, op_id: str) -> tuple[Connector, Operation]:
    op = await db.get(Operation, op_id)
    connector = (
        await db.execute(select(Connector).where(Connector.name == op_id.split(".", 1)[0]))
    ).scalar_one_or_none()
    if op is None or connector is None:
        raise MeasureError(f"operation {op_id} does not exist")
    if op.op_class != "read":
        raise MeasureError(f"operation {op_id} is not a read operation")
    if connector.review_status != "approved" or not connector.enabled:
        raise MeasureError(f"connector {connector.name} is not approved and enabled")
    return connector, op


async def _call(state: AppState, connector: Connector, op: Operation, args: dict[str, Any]) -> Any:
    if state.gateway is None:
        raise MeasureError("the tool gateway is not running")
    inv = await state.gateway.invoke(connector, op, args, "read")
    if not inv.ok:
        raise MeasureError(f"{op.id} failed: {inv.error or inv.status}")
    return inv.body


async def _jira_count(db: AsyncSession, state: AppState, jql: str) -> Measure:
    connector, op = await _read_op(db, "jira.issues.search")
    count, token = 0, None
    while count < JIRA_MAX:
        args: dict[str, Any] = {"jql": jql, "maxResults": JIRA_PAGE, "fields": "key"}
        if token:
            args["nextPageToken"] = token
        body = await _call(state, connector, op, args) or {}
        count += len(body.get("issues") or [])
        token = body.get("nextPageToken")
        if not token or body.get("isLast"):
            return Measure(float(count))
    return Measure(float(count), f"counted up to {JIRA_MAX}")


async def _projects(db: AsyncSession, agent: Agent, params: dict[str, Any]) -> list[str]:
    if params.get("project"):
        return [str(p) for p in (params["project"] if isinstance(params["project"], list) else [params["project"]])]
    rows = await db.execute(
        select(Assignment.object_ref).where(Assignment.agent_id == agent.id, Assignment.object_kind == "jira_project")
    )
    projects = [r for (r,) in rows.all()]
    if not projects:
        raise MeasureError("no Jira project: assign one to the agent or set params.project")
    return projects


def _jql_list(values: list[str]) -> str:
    return ", ".join('"' + v.replace('"', "") + '"' for v in values)


async def measure(db: AsyncSession, state: AppState, agent: Agent, manifest: ObjectiveManifest) -> Measure:
    metric = manifest.metric
    params: dict[str, Any] = dict(metric.params or {})
    at = now()
    if metric.source == "connector":
        connector, op = await _read_op(db, str(metric.operation))
        body = await _call(state, connector, op, params)
        return Measure(aggregate(pick(body, metric.path), metric.aggregate))
    query = metric.query
    if query == "open_proposals":
        n = await db.scalar(
            select(func.count()).where(ProposedAction.status == "pending", ProposedAction.agent == agent.name)
        )
        return Measure(float(n or 0))
    if query == "mission_failures":
        since = at - timedelta(hours=float(params.get("window_hours", 168)))
        rows = (
            await db.execute(
                select(Mission.plan).where(Mission.status == "failed", Mission.ended_at >= since).limit(5000)
            )
        ).scalars()
        return Measure(
            float(sum(1 for plan in rows if any(s.get("agent") == agent.name for s in plan.get("steps", []))))
        )
    if query == "untriaged_findings":
        before = at - timedelta(hours=float(params.get("older_than_hours", 48)))
        stmt = select(func.count()).where(Task.status == "incoming", Task.created_at <= before)
        if params.get("source"):
            stmt = stmt.where(Task.source == str(params["source"]))
        return Measure(float(await db.scalar(stmt) or 0))
    if query in {"stale_tickets", "overdue_tickets"}:
        scope = f"project in ({_jql_list(await _projects(db, agent, params))}) AND statusCategory != Done"
        if query == "stale_tickets":
            return await _jira_count(db, state, f"{scope} AND updated <= -{int(params.get('days', 30))}d")
        return await _jira_count(db, state, f"{scope} AND duedate < now()")
    raise MeasureError(f"unknown metric {query}")


# ---------------------------------------------------------------- suggestions


def _inputs_for(playbook: Playbook, manifest: ObjectiveManifest) -> dict[str, Any]:
    """Fill the playbook inputs it declares from the metric params and the objective scope."""
    schema = (playbook.manifest.get("inputs") or {}).get("schema") or {}
    props = set((schema.get("properties") or {}).keys())
    params = dict(manifest.metric.params or {})
    inputs = {k: v for k, v in params.items() if k in props}
    for item in manifest.scope:
        if item.kind.value in props and item.kind.value not in inputs:
            inputs[item.kind.value] = item.ref
    return inputs


def _estimate(playbook_manifest: dict[str, Any] | None, agents: dict[str, Agent], fallback: Agent) -> int:
    budget = (playbook_manifest or {}).get("budget") or {}
    if budget.get("max_tokens"):
        return int(budget["max_tokens"])
    names = {s["agent"] for s in (playbook_manifest or {}).get("steps", [])} or {fallback.name}
    return sum(
        int(((agents.get(n) or fallback).profile.get("limits") or {}).get("max_tokens", 200_000)) // 3 for n in names
    )


async def _spent(db: AsyncSession, objective: Objective, period: str, at: datetime) -> int:
    since = at - PERIODS[period]
    started: list[str] = []
    for (ids,) in (
        await db.execute(
            select(ObjectiveCheck.started_missions).where(
                ObjectiveCheck.objective_id == objective.id, ObjectiveCheck.ts >= since
            )
        )
    ).all():
        started += list(ids or [])
    if not started:
        return 0
    total = 0
    for mission in (await db.execute(select(Mission).where(Mission.id.in_(started)))).scalars():
        used = mission.tokens_in + mission.tokens_out
        # a mission still running holds its whole token budget until it ends
        total += used if mission.ended_at else max(used, int((mission.budget or {}).get("max_tokens") or 0))
    return total


async def _start_as_owner(
    db: AsyncSession, state: AppState, objective: Objective, playbook: Playbook, inputs: dict[str, Any], title: str
) -> str:
    owner = await db.get(User, objective.owner_user_id)
    if owner is None or owner.status != "active":
        raise Problem("forbidden", "the objective owner is not active")
    principal = await identity.build_principal(db, owner, state.config_auth, kind="objective")
    if not principal.can("missions:start", (principal.teams and sorted(principal.teams)[0]) or None):
        raise Problem("forbidden", "the objective owner cannot start missions")
    mission = await service.create(
        db,
        state,
        principal,
        service.MissionRequest(
            title=title,
            prompt=None,
            playbook=playbook,
            inputs=inputs,
            plan=None,
            connectors=[],
            data_class=None,
            autonomy=None,
            plan_first=False,
            budget={},
            session_id=None,
        ),
    )
    await service.start(db, state, mission)
    await audit.record(
        db,
        audit.Actor(f"objective:{objective.id}"),
        "mission.started",
        "mission",
        mission.id,
        team=mission.team_id,
        runs_as=owner.username,
        after={"objective": objective.id, "playbook": playbook.name},
    )
    return mission.id


async def _suggest(
    db: AsyncSession,
    state: AppState,
    objective: Objective,
    agent: Agent,
    manifest: ObjectiveManifest,
    explanation: str,
    at: datetime,
) -> tuple[list[str], list[str], list[str]]:
    """Returns (suggestion ids, started mission ids, notes)."""
    suggestions: list[str] = []
    started: list[str] = []
    notes: list[str] = []
    agents = {a.name: a for a in (await db.execute(select(Agent))).scalars()}
    open_rows = {
        s.playbook or "": s
        for s in (
            await db.execute(
                select(MissionSuggestion).where(
                    MissionSuggestion.objective_id == objective.id, MissionSuggestion.status == "open"
                )
            )
        ).scalars()
    }
    playbooks = (
        list((await db.execute(select(Playbook).where(Playbook.name.in_(manifest.allowed_playbooks)))).scalars())
        if manifest.allowed_playbooks
        else []
    )
    candidates: list[tuple[Playbook | None, dict[str, Any], bool, int]] = []
    for allowed in playbooks:
        inputs = _inputs_for(allowed, manifest)
        required = set(((allowed.manifest.get("inputs") or {}).get("schema") or {}).get("required") or [])
        if missing := sorted(required - set(inputs)):
            notes.append(f"{allowed.name} needs {', '.join(missing)}: set them in the metric params or the scope")
            continue
        has_writes = any(s.get("writes") for s in allowed.manifest.get("steps", []))
        candidates.append((allowed, inputs, has_writes, _estimate(allowed.manifest, agents, agent)))
    if not candidates:
        candidates.append((None, {"prompt": f"{manifest.title}. {explanation}"}, False, _estimate(None, agents, agent)))
    budget = manifest.budget_per_period
    spent = await _spent(db, objective, budget.period, at) if budget else 0
    for pb, inputs, has_writes, estimate in candidates:
        key = pb.name if pb else ""
        title = (
            f"{manifest.title}: {pb.manifest.get('description', pb.name)}" if pb else f"Close the gap: {manifest.title}"
        )
        if key in open_rows:
            open_rows[key].explanation, open_rows[key].inputs = explanation, inputs
            suggestions.append(open_rows[key].id)
            continue
        row = MissionSuggestion(
            id=new_id("sug"),
            objective_id=objective.id,
            agent=agent.name,
            title=title[:300],
            explanation=explanation,
            playbook=pb.name if pb else None,
            inputs=inputs,
            estimated_tokens=estimate,
            has_writes=has_writes,
        )
        db.add(row)
        suggestions.append(row.id)
        if pb is None or has_writes or manifest.mode != ObjectiveMode.auto_start_read_only:
            continue
        if budget and spent + estimate > budget.tokens:
            notes.append(f"{pb.name} not started: it would pass the objective budget for the {budget.period}")
            continue
        try:
            async with db.begin_nested():
                mission_id = await _start_as_owner(db, state, objective, pb, inputs, title[:200])
        except Problem as exc:
            notes.append(f"{pb.name} not started: {exc.detail or exc.kind}")
            continue
        row.status, row.mission_id = "accepted", mission_id
        started.append(mission_id)
        spent += estimate
    await db.flush()
    return suggestions, started, notes


# ---------------------------------------------------------------- evaluation


async def evaluate(db: AsyncSession, state: AppState, objective: Objective, actor: audit.Actor) -> ObjectiveCheck:
    """Measure the objective now and act on the gap. Raises Problem(unavailable) when it cannot be measured."""
    at = now()
    manifest = ObjectiveManifest.model_validate(objective.manifest)
    agent = await db.get(Agent, objective.agent_id)
    if agent is None:
        raise Problem("not-found", "the objective's agent no longer exists")
    try:
        result = await measure(db, state, agent, manifest)
    except MeasureError as exc:
        objective.next_check_at = next_check(manifest, at)
        await audit.record(
            db, actor, "objective.checked", "objective", objective.id, result="error", after={"error": str(exc)}
        )
        raise Problem("unavailable", f"the objective could not be measured: {exc}") from exc
    target = manifest.metric.target
    status = status_for(result.value, target, manifest.metric.direction)
    word = {"down": "at most", "up": "at least"}[manifest.metric.direction]
    explanation = (
        f"{manifest.metric.query or manifest.metric.operation} is {result.value:g}; the target is {word} {target:g}."
    )
    if result.note:
        explanation += f" ({result.note})"
    suggestions: list[str] = []
    started: list[str] = []
    if status != ObjectiveStatus.on_track:
        suggestions, started, notes = await _suggest(db, state, objective, agent, manifest, explanation, at)
        if notes:
            explanation += " " + "; ".join(notes) + "."
    check = ObjectiveCheck(
        id=new_id("obc"),
        objective_id=objective.id,
        ts=at,
        value=result.value,
        target=target,
        status=status.value,
        explanation=explanation,
        suggestions=suggestions,
        started_missions=started,
    )
    db.add(check)
    objective.status, objective.last_value, objective.last_checked_at = status.value, result.value, at
    objective.next_check_at = next_check(manifest, at)
    await db.flush()
    await audit.record(
        db,
        actor,
        "objective.checked",
        "objective",
        objective.id,
        team=agent.owner_team_id,
        after={"value": result.value, "status": status.value, "suggestions": suggestions, "started": started},
    )
    return check


async def tick(state: AppState, at: datetime | None = None) -> list[str]:
    """Evaluate every due objective (cron cadence), each in its own transaction."""
    moment = at or now()
    async with state.sessions() as db:
        due = [
            oid
            for (oid,) in (
                await db.execute(
                    select(Objective.id).where(
                        Objective.status != ObjectiveStatus.paused.value, Objective.next_check_at <= moment
                    )
                )
            ).all()
        ]
    checked = []
    for oid in due:
        try:
            async with state.sessions() as db, db.begin():
                row = await db.get(Objective, oid, with_for_update=True)
                if row is None or row.status == ObjectiveStatus.paused.value or (row.next_check_at or moment) > moment:
                    continue
                await evaluate(db, state, row, audit.Actor("system:objectives"))
                checked.append(oid)
        except Problem as exc:
            log.warning("objective %s: %s", oid, exc.detail)
            async with state.sessions() as db, db.begin():  # do not retry every tick
                row = await db.get(Objective, oid, with_for_update=True)
                if row is not None:
                    row.next_check_at = next_check(ObjectiveManifest.model_validate(row.manifest), moment)
    return checked


async def on_event(state: AppState, kind: str) -> list[str]:
    """Evaluate the objectives whose cadence is the event `kind` (a trigger kind that just fired)."""
    async with state.sessions() as db:
        rows = list(
            (await db.execute(select(Objective).where(Objective.status != ObjectiveStatus.paused.value))).scalars()
        )
        ids = [
            r.id
            for r in rows
            if isinstance(r.manifest.get("cadence"), dict) and r.manifest["cadence"].get("event") == kind
        ]
    checked = []
    for oid in ids:
        try:
            async with state.sessions() as db, db.begin():
                row = await db.get(Objective, oid, with_for_update=True)
                if row is not None:
                    await evaluate(db, state, row, audit.Actor("system:objectives"))
                    checked.append(oid)
        except Problem as exc:
            log.warning("objective %s: %s", oid, exc.detail)
    return checked


async def record_report(
    db: AsyncSession,
    objective: Objective,
    agent: Agent,
    value: float,
    explanation: str,
    suggested: list[tuple[str, str | None, dict[str, Any]]],
    actor: audit.Actor,
) -> ObjectiveCheck:
    """`report_objective` from an agent: its own measurement and the missions it proposes (never started here)."""
    manifest = ObjectiveManifest.model_validate(objective.manifest)
    if manifest.agent != agent.name:
        raise Problem("forbidden", "the objective belongs to another agent")
    status = status_for(value, manifest.metric.target, manifest.metric.direction)
    ids = []
    for title, playbook, inputs in suggested[:5]:
        if playbook is not None and playbook not in manifest.allowed_playbooks:
            raise Problem("validation", f"playbook {playbook} is not allowed for this objective")
        has_writes = False
        if playbook:
            pb = (await db.execute(select(Playbook).where(Playbook.name == playbook))).scalar_one_or_none()
            if pb is None:
                raise Problem("validation", f"no playbook {playbook}")
            has_writes = any(s.get("writes") for s in pb.manifest.get("steps", []))
        row = MissionSuggestion(
            id=new_id("sug"),
            objective_id=objective.id,
            agent=agent.name,
            title=title[:300],
            explanation=explanation[:4000],
            playbook=playbook,
            inputs=inputs if playbook else {"prompt": inputs.get("prompt") or title},
            has_writes=has_writes,
        )
        db.add(row)
        ids.append(row.id)
    check = ObjectiveCheck(
        id=new_id("obc"),
        objective_id=objective.id,
        ts=now(),
        value=value,
        target=manifest.metric.target,
        status=status.value,
        explanation=explanation[:4000],
        suggestions=ids,
        started_missions=[],
    )
    db.add(check)
    objective.status, objective.last_value, objective.last_checked_at = status.value, value, check.ts
    await db.flush()
    await audit.record(
        db,
        actor,
        "objective.reported",
        "objective",
        objective.id,
        team=agent.owner_team_id,
        after={"value": value, "status": status.value, "suggestions": ids},
    )
    return check
