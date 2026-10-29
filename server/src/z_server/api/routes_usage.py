"""Inference and usage, budgets, providers, analytics and the global live stream (docs/03 §5, docs/06)."""

from __future__ import annotations

import contextlib
import csv
import io
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Header
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import delete, or_, select
from z_common.models import api_ops as m
from z_common.models.enums import DataClass

from z_server import events, inference_credential, usage
from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.db.models import Budget, Event, Mission, Playbook, ProposedAction, Provider
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.llm import inference
from z_server.state import AppState

router = ContractRouter()
DEFAULT_WINDOW = timedelta(hours=24)
KEEPALIVE_S = 15.0


def _window(
    start: datetime | None, end: datetime | None, default: timedelta = DEFAULT_WINDOW
) -> tuple[datetime, datetime]:
    to = end or datetime.now(UTC)
    return (start or to - default), to


def _team_scope(ctx: Ctx, permission: str) -> list[str] | None:
    teams = ctx.user.teams_with(permission)
    return None if teams is None else sorted(teams)


@router.operation("getUsage")
async def get_usage(
    ctx: CtxDep,
    group_by: str = "model",
    to: datetime | None = None,
    bucket: str = "hour",
    format: str | None = None,
) -> Any:
    start = ctx.request.query_params.get("from")
    begin, end = _window(datetime.fromisoformat(start) if start else None, to)
    teams = _team_scope(ctx, "usage:read")
    scope = {"team": teams} if teams is not None else None
    series = await usage.aggregate(ctx.db, group_by, begin, end, bucket, scope)
    caps = {b.scope: b.limit for b in (await ctx.db.execute(select(Budget))).scalars()}
    for s in series:
        s["cap"] = caps.get(f"{group_by}:{s['key']}") if group_by in {"team", "user", "playbook", "agent"} else None
    if format == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([group_by, "bucket", "tokens_in", "tokens_out", "cached_tokens", "requests"])
        for s in series:
            for b in s["buckets"]:
                writer.writerow(
                    [
                        s.get("label") or s["key"],
                        b["ts"].isoformat(),
                        b["tokens_in"],
                        b["tokens_out"],
                        b["cached_tokens"],
                        b["requests"],
                    ]
                )
        return Response(
            out.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="z-usage.csv"'}
        )
    return m.Usage.model_validate({"group_by": group_by, "bucket": bucket, "from": begin, "to": end, "series": series})


async def _budget_out(ctx: Ctx, row: Budget) -> m.Budget:
    spent = await usage.used(ctx.db, row.scope, row.period, datetime.now(UTC))
    return m.Budget(
        id=row.id, scope=row.scope, limit=row.limit, period=row.period, alert_at=row.alert_at, used=spent, hard=row.hard
    )


@router.operation("listBudgets")
async def list_budgets(ctx: CtxDep) -> list[m.Budget]:
    rows = list((await ctx.db.execute(select(Budget).order_by(Budget.scope))).scalars())
    return [await _budget_out(ctx, r) for r in rows]


@router.operation("putBudgets")
async def put_budgets(ctx: CtxDep, body: list[m.BudgetWrite]) -> list[m.Budget]:
    """Replace the budget set (one budget per scope and period)."""
    keys = [(b.scope, b.period.value) for b in body]
    if len(keys) != len(set(keys)):
        raise Problem("validation", "one budget per scope and period")
    before = [
        {"scope": b.scope, "limit": b.limit, "period": b.period, "hard": b.hard}
        for b in (await ctx.db.execute(select(Budget))).scalars()
    ]
    await ctx.db.execute(delete(Budget))
    for b in body:
        ctx.db.add(
            Budget(
                id=new_id("bud"), scope=b.scope, limit=b.limit, period=b.period.value, alert_at=b.alert_at, hard=b.hard
            )
        )
    await ctx.db.flush()
    await ctx.audit("budgets.replaced", "budget", None, before=before, after=[b.dump() for b in body])
    return await list_budgets(ctx)


@router.operation("listProviders")
async def list_providers(ctx: CtxDep) -> list[m.Provider]:
    return [
        m.Provider.model_validate({k: p[k] for k in ("id", "name", "kind", "aliases", "data_classes", "enabled")})
        for p in await inference.providers(ctx.db, ctx.state.config)
    ]


@router.operation("createProvider", status_code=201)
async def create_provider(ctx: CtxDep, body: m.ProviderWrite) -> m.Provider:
    """Declares routing metadata for aliases LiteLLM already serves (config/litellm.yaml holds the endpoints)."""
    if body.kind == "external" and {DataClass.C2, DataClass.C3} & set(body.data_classes):
        raise Problem("validation", "external providers can never receive C2 or C3 data (invariant 2)")
    if (await ctx.db.execute(select(Provider).where(Provider.name == body.name))).scalar_one_or_none():
        raise Problem("conflict", f"provider {body.name!r} exists")
    row = Provider(
        id=new_id("prov"),
        name=body.name,
        kind=body.kind,
        aliases=list(body.aliases),
        data_classes=[c.value for c in body.data_classes],
        enabled=body.enabled,
    )
    ctx.db.add(row)
    await ctx.db.flush()
    await ctx.audit("provider.created", "provider", row.id, after=body.dump())
    return m.Provider.model_validate(
        {
            "id": row.id,
            "name": row.name,
            "kind": row.kind,
            "aliases": row.aliases,
            "data_classes": row.data_classes,
            "enabled": row.enabled,
        }
    )


@router.operation("getInferenceCredential")
async def get_inference_credential(ctx: CtxDep) -> m.InferenceCredential:
    return m.InferenceCredential.model_validate(await inference_credential.status(ctx.db, ctx.state))


@router.operation("putInferenceCredential")
async def put_inference_credential(ctx: CtxDep, body: m.InferenceCredentialWrite) -> m.InferenceCredential:
    """ADR 0027: the key goes to the LiteLLM gateway only; the audit keeps its fingerprint, never the key."""
    out = await inference_credential.replace(ctx.db, ctx.state, ctx.user.username, body.api_key, body.test)
    check = out.get("check") or {}
    await ctx.audit(
        "inference.credential.replaced",
        "setting",
        inference_credential.SETTING,
        after={"fingerprint": out["fingerprint"], "checked": bool(check), "check_ok": check.get("ok")},
    )
    return m.InferenceCredential.model_validate(out)


@router.operation("testInferenceCredential")
async def test_inference_credential(ctx: CtxDep) -> m.InferenceCredential:
    out = await inference_credential.retest(ctx.db, ctx.state)
    check = out.get("check") or {}
    await ctx.audit(
        "inference.credential.tested",
        "setting",
        inference_credential.SETTING,
        after={"model": check.get("model"), "ok": check.get("ok")},
    )
    return m.InferenceCredential.model_validate(out)


@router.operation("inferenceHealth")
async def inference_health(ctx: CtxDep) -> m.InferenceHealth:
    return m.InferenceHealth.model_validate(await inference.health(ctx.db, ctx.state))


@router.operation("inferenceModels")
async def inference_models(ctx: CtxDep) -> list[m.InferenceModel]:
    return [m.InferenceModel.model_validate(x) for x in (await inference.health(ctx.db, ctx.state))["models"]]


@router.operation("inferenceNodes")
async def inference_nodes(ctx: CtxDep) -> list[m.InferenceNode]:
    return [m.InferenceNode.model_validate(x) for x in (await inference.health(ctx.db, ctx.state))["nodes"]]


@router.operation("getAnalytics")
async def get_analytics(ctx: CtxDep, to: datetime | None = None, team: str | None = None) -> m.Analytics:
    start = ctx.request.query_params.get("from")
    begin, end = _window(datetime.fromisoformat(start) if start else None, to, timedelta(days=30))
    missions = select(Mission).where(Mission.created_at >= begin, Mission.created_at < end)
    teams = _team_scope(ctx, "analytics:read")
    if teams is not None:
        missions = missions.where(Mission.team_id.in_(teams))
    if team:
        missions = missions.where(Mission.team_id == team)
    rows = list((await ctx.db.execute(missions)).scalars())
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    baselines = {
        p.name: int(p.manifest.get("baseline_minutes") or 0) for p in (await ctx.db.execute(select(Playbook))).scalars()
    }
    completed: dict[str, int] = {}
    for r in rows:
        if r.status == "succeeded" and r.playbook_name:
            completed[r.playbook_name] = completed.get(r.playbook_name, 0) + 1
    hours = [
        {
            "playbook": p,
            "completed": n,
            "baseline_minutes": baselines.get(p, 0),
            "hours": round(n * baselines.get(p, 0) / 60, 2),
        }
        for p, n in sorted(completed.items())
    ]
    ids = [r.id for r in rows]
    actions = (
        list((await ctx.db.execute(select(ProposedAction).where(ProposedAction.mission_id.in_(ids)))).scalars())
        if ids
        else []
    )
    decided = [
        a for a in actions if a.status in {"approved", "applying", "applied", "failed", "rejected", "superseded"}
    ]
    approved = [a for a in decided if a.status in {"approved", "applying", "applied", "failed"}]
    failures: dict[str, int] = {}
    for a in actions:
        if a.status == "failed":
            failures[a.connector] = failures.get(a.connector, 0) + 1
    tokens = await usage.aggregate(ctx.db, "team", begin, end, "day", {"team": teams} if teams is not None else None)
    return m.Analytics.model_validate(
        {
            "from": begin,
            "to": end,
            "hours_saved": hours,
            "approval_rate": round(len(approved) / len(decided), 4) if decided else None,
            "edit_rate": round(sum(1 for a in decided if a.status == "superseded") / len(decided), 4)
            if decided
            else None,
            "failures_by_connector": [
                {"connector": c, "failures": n} for c, n in sorted(failures.items(), key=lambda x: -x[1])
            ],
            "tokens_by_team": [
                {"team": t.get("label") or t["key"], "tokens": t["tokens_in"] + t["tokens_out"], "cost": None}
                for t in tokens
            ],
            "missions_by_status": by_status,
        }
    )


# ---------------------------------------------------------------- global stream


# A transaction that appended events stamps its mission's updated_at when it ran, not when it committed: missions
# touched within this margin are looked at again, so a slow transaction is never missed.
DISCOVERY_MARGIN = timedelta(minutes=10)
PAGE = 500


async def _global(
    state: AppState, visible: list[str] | None, user_id: str, after: datetime, resume: tuple[str, int] | None = None
) -> AsyncIterator[str]:
    """Every event of the missions the user may see; `id` = `<iso ts>|<mission>|<seq>`.

    Paged per mission on the sequence, never on the event time (the runner sets `ts`, and transactions commit
    in any order): `missions.next_seq` is committed with the events below it, so every event under it is
    visible and none is skipped. A mission seen for the first time is read from `after` on."""
    yield "retry: 3000\n\n"
    seen: dict[str, int] = {resume[0]: resume[1] + 1} if resume else {}
    since = min(after, datetime.now(UTC)) - DISCOVERY_MARGIN
    while True:
        polled_at = datetime.now(UTC)
        async with state.sessions() as db:
            stmt = select(Mission.id, Mission.next_seq).where(Mission.updated_at >= since)
            if visible is not None:
                stmt = stmt.where(or_(Mission.created_by == user_id, Mission.team_id.in_(visible)))
            behind = [(mid, top) for mid, top in (await db.execute(stmt)).all() if top > seen.get(mid, -1)]
            rows: list[Event] = []
            for mid, top in behind:
                q = select(Event).where(Event.mission_id == mid, Event.seq < top).order_by(Event.seq).limit(PAGE)
                q = q.where(Event.seq >= seen[mid]) if mid in seen else q.where(Event.ts >= after)
                batch = list((await db.execute(q)).scalars())
                rows.extend(batch)
                seen[mid] = batch[-1].seq + 1 if len(batch) == PAGE else top
        rows.sort(key=lambda r: (r.ts, r.mission_id, r.seq))
        for row in rows:
            data = json.dumps(events.as_contract(row), separators=(",", ":"), default=str)
            yield f"id: {row.ts.isoformat()}|{row.mission_id}|{row.seq}\nevent: {row.type}\ndata: {data}\n\n"
        since = polled_at - DISCOVERY_MARGIN
        if any(seen[mid] < top for mid, top in behind):
            continue  # a mission had more than a page
        await state.hub.wait(None, timeout=KEEPALIVE_S)
        yield ": keepalive\n\n"


@router.operation("globalStream")
async def global_stream(ctx: CtxDep, last_event_id: Annotated[str | None, Header()] = None) -> StreamingResponse:
    if not ctx.user.can("missions:read"):
        raise Problem("forbidden", "missing missions:read", permission="missions:read")
    after, resume = datetime.now(UTC), None
    if last_event_id:
        with contextlib.suppress(ValueError):
            ts, mission_id, seq = last_event_id.split("|", 2)
            after, resume = datetime.fromisoformat(ts), (mission_id, int(seq))
    visible = _team_scope(ctx, "missions:read")
    return StreamingResponse(
        _global(ctx.state, visible, ctx.user.user_id, after, resume),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
