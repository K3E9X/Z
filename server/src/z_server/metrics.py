"""Z metrics on `/metrics` (docs/06 §5 "Export"), read from the database at each scrape.

- `z_tokens_total{model,agent,team,direction}`: tokens recorded in usage (hourly buckets, never deleted), so
  the sum only grows: a counter. `agent` is the agent name, or `z:<purpose>` for the platform's own calls.
- `z_missions_total{status}`: missions that ended, by final status (a counter: terminal statuses never change);
  `z_missions_active{status}`: missions not ended yet, by status.
- `z_jobs{status}`: jobs not ended, by status (queued, running, waiting, needs_attention...).
- `z_review_pending{kind}`: actions waiting for approval and open agent questions.
- `z_connector_up{connector}`: 1 when the health monitor last saw the connector healthy, degraded or with a token
  expiring soon, 0 when unreachable or disabled.
- `z_notifications_pending`: notification outbox rows not yet delivered.

Every replica reports the same values (they come from the shared database): aggregate with `max`, not `sum`.
Labels are low-cardinality on purpose: no mission, job or user ids.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import func, select
from z_common.models.enums import ActionStatus, JobStatus, MissionStatus

from z_server.db.models import Connector, Job, Mission, NotificationOutbox, ProposedAction, Question, Team, UsageRecord

if TYPE_CHECKING:
    from z_server.state import AppState

UP = {"healthy", "degraded", "token_expiring"}


@dataclass
class Snapshot:
    tokens: list[tuple[str, str, str, str, int]] = field(default_factory=list)  # model, agent, team, direction, n
    missions_ended: dict[str, int] = field(default_factory=dict)
    missions_active: dict[str, int] = field(default_factory=dict)
    jobs: dict[str, int] = field(default_factory=dict)
    review: dict[str, int] = field(default_factory=dict)
    connectors: dict[str, int] = field(default_factory=dict)
    notifications_pending: int = 0


async def snapshot(state: AppState) -> Snapshot:
    snap = Snapshot()
    async with state.sessions() as db:
        teams = {t.id: t.name for t in (await db.execute(select(Team))).scalars()}
        rows = await db.execute(
            select(
                UsageRecord.model,
                UsageRecord.agent,
                UsageRecord.team,
                func.sum(UsageRecord.tokens_in),
                func.sum(UsageRecord.tokens_out),
            )
            .where(UsageRecord.bucket == "hour")
            .group_by(UsageRecord.model, UsageRecord.agent, UsageRecord.team)
        )
        for model, agent, team, tokens_in, tokens_out in rows.all():
            team_name = teams.get(team, team) if team else ""
            snap.tokens.append((model or "", agent or "", team_name, "in", int(tokens_in or 0)))
            snap.tokens.append((model or "", agent or "", team_name, "out", int(tokens_out or 0)))
        for status, count in (await db.execute(select(Mission.status, func.count()).group_by(Mission.status))).all():
            terminal = status in MissionStatus.__members__ and MissionStatus(status).terminal
            (snap.missions_ended if terminal else snap.missions_active)[status] = int(count)
        for status, count in (await db.execute(select(Job.status, func.count()).group_by(Job.status))).all():
            if status in JobStatus.__members__ and not JobStatus(status).terminal:
                snap.jobs[status] = int(count)
        snap.review["action"] = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ProposedAction)
                    .where(ProposedAction.status == ActionStatus.pending.value)
                )
            ).scalar_one()
        )
        snap.review["question"] = int(
            (
                await db.execute(select(func.count()).select_from(Question).where(Question.answered_at.is_(None)))
            ).scalar_one()
        )
        for name, enabled, health in (
            await db.execute(select(Connector.name, Connector.enabled, Connector.health))
        ).all():
            snap.connectors[name] = int(bool(enabled) and str((health or {}).get("status")) in UP)
        snap.notifications_pending = int(
            (
                await db.execute(
                    select(func.count()).select_from(NotificationOutbox).where(NotificationOutbox.done.is_(False))
                )
            ).scalar_one()
        )
    return snap


class ZCollector(Collector):
    """Serves the last snapshot; `/metrics` refreshes it before rendering."""

    def __init__(self) -> None:
        self.snap = Snapshot()

    def collect(self) -> Iterator[CounterMetricFamily | GaugeMetricFamily]:
        snap = self.snap
        tokens = CounterMetricFamily(
            "z_tokens", "Tokens recorded in usage", labels=["model", "agent", "team", "direction"]
        )
        for model, agent, team, direction, n in snap.tokens:
            tokens.add_metric([model, agent, team, direction], n)
        yield tokens
        ended = CounterMetricFamily("z_missions", "Missions that ended, by final status", labels=["status"])
        for status, n in sorted(snap.missions_ended.items()):
            ended.add_metric([status], n)
        yield ended
        yield _gauge("z_missions_active", "Missions not ended, by status", "status", snap.missions_active)
        yield _gauge("z_jobs", "Jobs not ended, by status", "status", snap.jobs)
        yield _gauge("z_review_pending", "Actions and questions waiting for a person", "kind", snap.review)
        yield _gauge("z_connector_up", "1 when the connector is reachable", "connector", snap.connectors)
        pending = GaugeMetricFamily("z_notifications_pending", "Notification outbox rows not yet delivered")
        pending.add_metric([], snap.notifications_pending)
        yield pending


def _gauge(name: str, doc: str, label: str, values: dict[str, int]) -> GaugeMetricFamily:
    gauge = GaugeMetricFamily(name, doc, labels=[label])
    for key, n in sorted(values.items()):
        gauge.add_metric([key], n)
    return gauge
