"""Token usage (docs/06 §4): recorded per hour from the runners' `usage` events, aggregated for the Inference
screen and checked against budgets (org, team, user, playbook) before a mission starts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from z_server.db.models import Budget, Mission, Team, UsageRecord
from z_server.errors import Problem

GROUP_COLUMNS = {
    "model": UsageRecord.model,
    "agent": UsageRecord.agent,
    "mission": UsageRecord.mission_id,
    "team": UsageRecord.team,
    "user": UsageRecord.user_id,
    "playbook": UsageRecord.playbook,
}


def hour(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


async def record(
    db: AsyncSession,
    mission: Mission,
    agent: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cached: int,
    at: datetime,
) -> None:
    values = {
        "bucket": "hour",
        "ts_bucket": hour(at),
        "model": model[:64],
        "agent": agent[:64],
        "mission_id": mission.id,
        "playbook": (mission.playbook_name or "")[:64],
        "team": mission.team_id or "",
        "user_id": mission.created_by,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cached_tokens": cached,
        "requests": 1,
    }
    stmt = insert(UsageRecord).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_usage_records_key",
        set_={
            "tokens_in": UsageRecord.tokens_in + stmt.excluded.tokens_in,
            "tokens_out": UsageRecord.tokens_out + stmt.excluded.tokens_out,
            "cached_tokens": UsageRecord.cached_tokens + stmt.excluded.cached_tokens,
            "requests": UsageRecord.requests + 1,
        },
    )
    await db.execute(stmt)


async def aggregate(
    db: AsyncSession, group_by: str, start: datetime, end: datetime, bucket: str, scope: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    if group_by not in GROUP_COLUMNS:
        raise Problem("validation", f"group_by must be one of {', '.join(GROUP_COLUMNS)}")
    if bucket not in {"hour", "day"}:
        raise Problem("validation", "usage is recorded per hour: use bucket=hour or bucket=day")
    key = GROUP_COLUMNS[group_by]
    ts = func.date_trunc(bucket, UsageRecord.ts_bucket).label("ts")
    stmt = (
        select(
            key.label("key"),
            ts,
            func.sum(UsageRecord.tokens_in),
            func.sum(UsageRecord.tokens_out),
            func.sum(UsageRecord.cached_tokens),
            func.sum(UsageRecord.requests),
        )
        .where(UsageRecord.bucket == "hour", UsageRecord.ts_bucket >= hour(start), UsageRecord.ts_bucket < end)
        .group_by(key, ts)
        .order_by(key, ts)
    )
    for column, value in (scope or {}).items():
        stmt = stmt.where(
            GROUP_COLUMNS[column].in_(value)
            if isinstance(value, list | set | tuple)
            else GROUP_COLUMNS[column] == value
        )
    series: dict[str, dict[str, Any]] = {}
    for k, at, tin, tout, cached, requests in (await db.execute(stmt)).all():
        s = series.setdefault(
            k or "(none)",
            {"key": k or "(none)", "buckets": [], "tokens_in": 0, "tokens_out": 0, "cached_tokens": 0, "requests": 0},
        )
        s["buckets"].append(
            {
                "ts": at,
                "tokens_in": int(tin),
                "tokens_out": int(tout),
                "cached_tokens": int(cached),
                "requests": int(requests),
            }
        )
        for name, v in (("tokens_in", tin), ("tokens_out", tout), ("cached_tokens", cached), ("requests", requests)):
            s[name] += int(v)
    if group_by == "team":
        names = {t.id: t.name for t in (await db.execute(select(Team))).scalars()}
        for s in series.values():
            s["label"] = names.get(s["key"], s["key"])
    return sorted(series.values(), key=lambda s: -(s["tokens_in"] + s["tokens_out"]))


def period_start(period: str, at: datetime) -> datetime | None:
    if period == "day":
        return at.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        day = at.replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=day.weekday())
    if period == "month":
        return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None  # per mission: checked against the mission's own tokens


async def used(db: AsyncSession, scope: str, period: str, at: datetime) -> int:
    start = period_start(period, at)
    if start is None:
        return 0
    stmt = select(func.coalesce(func.sum(UsageRecord.tokens_in + UsageRecord.tokens_out), 0)).where(
        UsageRecord.bucket == "hour", UsageRecord.ts_bucket >= start
    )
    kind, _, value = scope.partition(":")
    column = {
        "team": UsageRecord.team,
        "user": UsageRecord.user_id,
        "playbook": UsageRecord.playbook,
        "mission": UsageRecord.mission_id,
        "agent": UsageRecord.agent,
    }.get(kind)
    if kind != "org":
        if column is None:
            return 0
        stmt = stmt.where(column == value)
    return int((await db.execute(stmt)).scalar_one())


def scopes_for(mission: Mission) -> list[str]:
    out = ["org", f"user:{mission.created_by}"]
    if mission.team_id:
        out.append(f"team:{mission.team_id}")
    if mission.playbook_name:
        out.append(f"playbook:{mission.playbook_name}")
    return out


async def check_start(db: AsyncSession, mission: Mission, at: datetime, config_org_monthly: int | None = None) -> None:
    """Hard budgets block a start once spent (budget-exceeded); soft ones only alert."""
    wanted = scopes_for(mission)
    budgets = list((await db.execute(select(Budget).where(Budget.scope.in_(wanted)))).scalars())
    if config_org_monthly and not any(b.scope == "org" and b.period == "month" for b in budgets):
        budgets.append(
            Budget(id="cfg-org", scope="org", limit=config_org_monthly, period="month", alert_at=0.8, hard=True)
        )
    for budget in budgets:
        if not budget.hard or budget.period == "mission":
            continue
        spent = await used(db, budget.scope, budget.period, at)
        if spent >= budget.limit:
            raise Problem(
                "budget-exceeded",
                f"{budget.scope} has used {spent} of its {budget.limit} tokens this {budget.period}",
                scope=budget.scope,
            )
