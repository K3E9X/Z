"""Routing policy, learning signals and eval runs (docs/06 §3, docs/08)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from z_common.models.enums import DataClass

from z_server import evals, learner
from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.api.paging import page
from z_server.db.models import EvalRun, Proposal, RoutingRule, Setting, Signal
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.llm import policy

router = ContractRouter()
MAX_MODELS = 4


def rule_out(row: RoutingRule) -> dict[str, Any]:
    return {"id": row.id, "match": row.match, "target": row.target, "origin": row.origin, "version": row.version}


@router.operation("getRoutingPolicy")
async def get_routing_policy(ctx: CtxDep) -> dict[str, Any]:
    out = await policy.effective(ctx.db, ctx.state.config)
    return {
        **{k: out[k] for k in policy.FIELDS if k in out},
        "rules": [rule_out(r) for r in await policy.rules(ctx.db)],
    }


@router.operation("putRoutingPolicy")
async def put_routing_policy(ctx: CtxDep, body: dict[str, Any]) -> dict[str, Any]:
    policy.validate(body)
    unknown = set(body.get("data_classes") or {}) - {c.value for c in DataClass}
    if unknown:
        raise Problem("validation", f"unknown data classes: {', '.join(sorted(unknown))}")
    before = await get_routing_policy(ctx)
    row = await ctx.db.get(Setting, policy.SETTING, with_for_update=True)
    value = {k: body[k] for k in policy.FIELDS if k in body}
    if row is None:
        ctx.db.add(Setting(key=policy.SETTING, value=value))
    else:
        row.value = value
    if "rules" in body:
        current = {r.id: r for r in await policy.rules(ctx.db, active_only=False)}
        kept: set[str] = set()
        for item in body["rules"] or []:
            target = {k: v for k, v in (item.get("target") or {}).items() if v}
            if not item.get("match") or not target:
                raise Problem("validation", "a routing rule needs a match and an agent or a playbook")
            existing = current.get(str(item.get("id") or ""))
            if existing is None:
                rule = RoutingRule(id=new_id("rte"), match=item["match"], target=target, origin="config", version=1)
                ctx.db.add(rule)
                kept.add(rule.id)
                continue
            if (existing.match, existing.target) != (item["match"], target) or not existing.active:
                existing.match, existing.target, existing.active = item["match"], target, True
                existing.version += 1
            kept.add(existing.id)
        for rule_id, rule in current.items():
            if rule_id not in kept:
                rule.active = False  # kept for history and rollback
    await ctx.db.flush()
    after = await get_routing_policy(ctx)
    await ctx.audit("routing_policy.updated", "routing_policy", None, before=before, after=after)
    return after


def signal_out(row: Signal) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "mission_id": row.mission_id,
        "job_id": row.job_id,
        "agent": row.agent,
        "skill": row.skill,
        "payload": row.payload,
        "ts": row.ts,
    }


@router.operation("listSignals")
async def list_signals(
    ctx: CtxDep, cursor: str | None = None, limit: int = 50, agent: str | None = None, kind: str | None = None
) -> dict[str, Any]:
    stmt = select(Signal)
    if agent:
        stmt = stmt.where(Signal.agent == agent)
    if kind:
        stmt = stmt.where(Signal.kind == kind)
    rows, nxt = await page(ctx.db, stmt, Signal.id, cursor, limit)
    return {"items": [signal_out(r) for r in rows], "next_cursor": nxt}


@router.operation("runEvals", status_code=202)
async def run_evals(ctx: CtxDep, body: dict[str, Any]) -> list[dict[str, Any]]:
    target = str(body["target"])
    models = list(dict.fromkeys(body.get("models") or []))
    if len(models) > MAX_MODELS:
        raise Problem("validation", f"compare at most {MAX_MODELS} models at once")
    if not models:
        agent_name = target.split(":", 1)[1] if target.startswith("agent:") else None
        default = None
        if agent_name:
            from z_server.db.models import Agent

            agent = (await ctx.db.execute(select(Agent).where(Agent.name == agent_name))).scalar_one_or_none()
            default = agent.profile.get("model") if agent else None
        models = [str(default or "z-fast")]
    runs = []
    for model in models:
        run = await evals.start(
            ctx.db, ctx.state, ctx.user, target, model, version=body.get("version"), case_ids=body.get("cases")
        )
        runs.append(evals.run_out(run))
    ctx.audited = True  # evals.start audits each run
    return runs


@router.operation("listEvalRuns")
async def list_eval_runs(
    ctx: CtxDep, cursor: str | None = None, limit: int = 50, target: str | None = None
) -> dict[str, Any]:
    stmt = select(EvalRun)
    if target:
        stmt = stmt.where(EvalRun.target == target)
    rows, nxt = await page(ctx.db, stmt, EvalRun.id, cursor, limit)
    return {"items": [evals.run_out(r) for r in rows], "next_cursor": nxt}


@router.operation("getEvalRun")
async def get_eval_run(ctx: CtxDep, run_id: str) -> dict[str, Any]:
    run = await ctx.db.get(EvalRun, run_id)
    if run is None:
        raise Problem("not-found", "no such eval run")
    return evals.run_out(run)


# ---------------------------------------------------------------- proposals


async def _proposal(ctx: Ctx, proposal_id: str, lock: bool = False) -> Proposal:
    row = await ctx.db.get(Proposal, proposal_id, with_for_update=lock)
    if row is None:
        raise Problem("not-found", "no such proposal")
    return row


@router.operation("listProposals")
async def list_proposals(
    ctx: CtxDep, cursor: str | None = None, limit: int = 50, status: str | None = None
) -> dict[str, Any]:
    stmt = select(Proposal)
    if status:
        stmt = stmt.where(Proposal.status.in_(status.split(",")))
    rows, nxt = await page(ctx.db, stmt, Proposal.id, cursor, limit)
    return {"items": [learner.proposal_out(r) for r in rows], "next_cursor": nxt}


@router.operation("getProposal")
async def get_proposal(ctx: CtxDep, id: str) -> dict[str, Any]:
    return learner.proposal_out(await _proposal(ctx, id))


@router.operation("applyProposal")
async def apply_proposal(ctx: CtxDep, id: str, body: dict[str, Any]) -> dict[str, Any]:
    row = await _proposal(ctx, id, lock=True)
    written = await learner.apply(ctx.db, ctx.state, row, ctx.user)
    await ctx.audit(
        "proposal.applied",
        "proposal",
        row.id,
        before={"status": "ready"},
        after={"kind": row.kind, "target": row.target, **written, "reason": body.get("reason")},
    )
    return learner.proposal_out(row)


@router.operation("rejectProposal")
async def reject_proposal(ctx: CtxDep, id: str, body: dict[str, Any]) -> dict[str, Any]:
    row = await _proposal(ctx, id, lock=True)
    if row.status not in learner.OPEN:
        raise Problem("conflict", f"the proposal is {row.status}")
    before = row.status
    row.status, row.decided_by, row.decided_at = "rejected", ctx.user.username, learner.now()
    await ctx.audit(
        "proposal.rejected", "proposal", row.id, before={"status": before}, after={"reason": body.get("reason")}
    )
    return learner.proposal_out(row)


@router.operation("tryProposal")
async def try_proposal(ctx: CtxDep, id: str, body: dict[str, Any]) -> Any:
    from z_server.api.routes_missions import mission_out

    row = await _proposal(ctx, id)
    mission = await learner.try_on_mission(ctx.db, ctx.state, row, ctx.user)
    await ctx.audit("proposal.tried", "proposal", row.id, after={"mission": mission.id})
    return await mission_out(ctx, mission)
