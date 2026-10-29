"""Intake loop (docs/05 §11): fires due schedule triggers, polls Jira for labelled issues, and checks due
objectives (docs/04 §3), including those whose cadence is an event that just fired.

Jira: every `interval_s` (default 300) the trigger searches `project = <p> AND labels = "<label>"` (or its own
`jql`) with the connector's read account, newest first, and creates one task per issue it has not seen.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from z_server import objectives
from z_server.db.models import Connector, Operation, Trigger
from z_server.gateway.core import shape
from z_server.intake import service as intake
from z_server.intake.cron import Cron, CronError
from z_server.state import AppState

log = logging.getLogger("z.intake")
JIRA_DEFAULT_INTERVAL_S = 300


async def fire_schedules(state: AppState, at: datetime | None = None) -> list[str]:
    now = at or datetime.now(UTC)
    fired = []
    async with state.sessions() as db, db.begin():
        due = list(
            (
                await db.execute(
                    select(Trigger)
                    .where(Trigger.kind == "schedule", Trigger.enabled.is_(True), Trigger.next_fire_at <= now)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        for trigger in due:
            try:
                cron = Cron(str(trigger.config.get("cron", "")), str(trigger.config.get("timezone", "UTC")))
            except CronError as exc:
                log.warning("trigger %s disabled: %s", trigger.id, exc)
                trigger.enabled = False
                continue
            scheduled = trigger.next_fire_at or now
            trigger.next_fire_at = cron.next_after(now)
            event = {
                "scheduled_for": scheduled.isoformat(),
                "fired_at": now.isoformat(),
                "cron": trigger.config.get("cron"),
            }
            result = await intake.fire(db, state, trigger, event, f"schedule:{scheduled.isoformat()}", now)
            fired.append(result.task.id)
    return fired


async def poll_jira(state: AppState, at: datetime | None = None) -> list[str]:
    now = at or datetime.now(UTC)
    created: list[str] = []
    async with state.sessions() as db:
        triggers = list(
            (await db.execute(select(Trigger).where(Trigger.kind == "jira", Trigger.enabled.is_(True)))).scalars()
        )
        connector = (await db.execute(select(Connector).where(Connector.name == "jira"))).scalar_one_or_none()
        op = await db.get(Operation, "jira.issues.search")
        db.expunge_all()
    if connector is None or op is None or not connector.enabled or state.gateway is None:
        return created
    for trigger in triggers:
        interval = int(trigger.config.get("interval_s", JIRA_DEFAULT_INTERVAL_S))
        if trigger.last_fired_at and trigger.last_fired_at + timedelta(seconds=interval) > now:
            continue
        jql = (
            trigger.config.get("jql")
            or f'project = "{trigger.config["project"]}" AND labels = "{trigger.config.get("label", "z")}" ORDER BY created DESC'
        )
        inv = await state.gateway.invoke(
            connector,
            op,
            {
                "jql": jql,
                "maxResults": 20,
                "fields": "summary,status,labels,priority,issuetype,reporter,created,description",
            },
            "read",
        )
        async with state.sessions() as db, db.begin():
            row = await db.get(Trigger, trigger.id, with_for_update=True)
            if row is None:
                continue
            row.last_fired_at = now
            if not inv.ok:
                log.warning("jira trigger %s: %s", trigger.id, inv.error)
                continue
            for issue in reversed((inv.body or {}).get("issues") or []):
                event = shape(issue)
                event.setdefault("title", f"{issue.get('key')}: {(issue.get('fields') or {}).get('summary', '')}")
                result = await intake.fire(db, state, row, event, str(issue.get("key")), now)
                if result.note != "already received":
                    created.append(result.task.id)
    return created


async def run_forever(state: AppState, interval_s: float = 30.0) -> None:
    while True:
        try:
            fired = await fire_schedules(state)
            created = await poll_jira(state)
            await objectives.tick(state)
            if fired:
                await objectives.on_event(state, "schedule")
            if created:
                await objectives.on_event(state, "jira")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("intake tick failed")
        await asyncio.sleep(interval_s)
