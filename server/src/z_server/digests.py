"""E-mail digests (docs/05 §10 "Digests (daily decisions, weekly report)"), ADR 0034.

`notifications.digest.daily_decisions` and `weekly_report` are five-field cron expressions, evaluated in UTC.
At each due time every active person who turned personal e-mail on (Settings → Notifications) gets:
- daily: the decisions waiting for them, i.e. pending writes they may approve and open questions they may
  answer, each with its link to Z; summaries, questions and titles only up to C1 (as personal notifications);
- weekly: their missions of the week by status, the writes proposed on them, the approvals they gave and the
  tokens their missions used (counts only).
Empty digests are not sent, nothing is sent without the admins' SMTP server, and the mails go through the
notification outbox (same delivery, retries and SMTP as every notification).

Once per period, whatever the replicas and restarts: one worker at a time holds a transaction-scoped advisory
lock, reads the last fire time sent per digest from `settings` (`digest_state`), enqueues the mails of the latest
due fire time and stores it, in the same transaction. A digest seen for the first time starts from now (no mail
for a period that ended before Z knew it); after downtime only the latest missed period is sent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import DataClass, MissionStatus

from z_server import notifications
from z_server.auth import identity
from z_server.db.models import ActionApproval, Mission, ProposedAction, Question, Setting, User
from z_server.intake.cron import Cron, CronError
from z_server.questions import may_answer
from z_server.rbac import Principal
from z_server.state import AppState

log = logging.getLogger("z.digests")
STATE_KEY = "digest_state"
LOCK_KEY = 0x5A_D16E5  # pg_try_advisory_xact_lock key: one digest run at a time across replicas
DIGESTS = ("daily_decisions", "weekly_report")
LOOKBACK = timedelta(days=35)  # the latest fire time of a monthly expression is at most this far back
MAX_STEPS = 60 * 24 * 36  # every minute of the lookback
MAX_ITEMS = 20
WEEK = timedelta(days=7)


def schedules(config: dict[str, Any]) -> dict[str, Cron]:
    raw = ((config.get("notifications") or {}).get("digest")) or {}
    out: dict[str, Cron] = {}
    for name in DIGESTS:
        expression = raw.get(name)
        if not expression:
            continue
        try:
            out[name] = Cron(str(expression), "UTC")
        except CronError as exc:
            log.warning("notifications.digest.%s is not a cron expression: %s", name, exc)
    return out


def latest_due(cron: Cron, last: datetime, at: datetime) -> datetime | None:
    """The latest fire time in (last, at], or None when nothing is due."""
    cursor, fire = max(last, at - LOOKBACK), None
    for _ in range(MAX_STEPS):
        following = cron.next_after(cursor)
        if following > at:
            break
        fire = cursor = following
    return fire


def _shown(mission: Mission) -> bool:
    try:
        return DataClass(mission.data_class).rank <= DataClass(notifications.PERSONAL_CONTENT_UP_TO).rank
    except ValueError:
        return False


async def recipients(db: AsyncSession) -> list[User]:
    """Active people with an address who turned personal e-mail notifications on."""
    rows = (await db.execute(select(Setting).where(Setting.key.startswith(notifications.PERSONAL_KEY)))).scalars()
    ids = sorted(r.key.removeprefix(notifications.PERSONAL_KEY) for r in rows if (r.value or {}).get("enabled"))
    if not ids:
        return []
    users = (await db.execute(select(User).where(User.id.in_(ids), User.status == "active"))).scalars()
    return [u for u in users if u.email and "\n" not in u.email]


def _may_approve(principal: Principal, action: ProposedAction, mission: Mission, approvers: set[str]) -> bool:
    """The rules of the review (routes_review.approve) that do not depend on the moment: scope, four eyes, one
    approval per person."""
    if not principal.can_on("actions:approve", mission.team_id):
        return False
    if action.four_eyes and mission.created_by == principal.user_id:
        return False
    return principal.user_id not in approvers


@dataclass
class Pending:
    """What waits for a decision, loaded once per run: open missions, their pending writes (with who already
    approved them) and their open questions."""

    missions: dict[str, Mission]
    actions: list[ProposedAction]
    approvals: dict[str, set[str]]  # action id -> user ids
    questions: list[Question]


async def pending(db: AsyncSession) -> Pending:
    terminal = [s.value for s in MissionStatus if s.terminal]
    missions = {
        m.id: m
        for m in (
            await db.execute(select(Mission).where(Mission.status.not_in(terminal), Mission.eval_run_id.is_(None)))
        ).scalars()
    }
    ids = list(missions)
    actions = list(
        (
            await db.execute(
                select(ProposedAction)
                .where(ProposedAction.status == "pending", ProposedAction.mission_id.in_(ids))
                .order_by(ProposedAction.created_at)
            )
        ).scalars()
    )
    approvals: dict[str, set[str]] = {}
    if actions:
        given = await db.execute(select(ActionApproval).where(ActionApproval.action_id.in_([a.id for a in actions])))
        for approval in given.scalars():
            approvals.setdefault(approval.action_id, set()).add(approval.user_id)
    questions = list(
        (
            await db.execute(
                select(Question)
                .where(Question.answer.is_(None), Question.mission_id.in_(ids))
                .order_by(Question.created_at)
            )
        ).scalars()
    )
    return Pending(missions, actions, approvals, questions)


async def daily(db: AsyncSession, state: AppState, user: User, waiting: Pending) -> dict[str, Any] | None:
    if not waiting.actions and not waiting.questions:
        return None
    principal = await identity.build_principal(db, user, state.config_auth, kind="digest")
    base = state.settings.public_url.rstrip("/")
    lines: list[str] = []
    writes = asked = 0
    for action in waiting.actions:
        mission = waiting.missions[action.mission_id]
        if not _may_approve(principal, action, mission, waiting.approvals.get(action.id, set())):
            continue
        writes += 1
        if len(lines) < MAX_ITEMS:
            what = action.summary if _shown(mission) else f"details in Z (data class {mission.data_class})"
            where = f" ({mission.title})" if _shown(mission) else ""
            lines.append(f"- Write to approve, {action.operation}: {what}{where}\n  {base}/review?action={action.id}")
    for question in waiting.questions:
        mission = waiting.missions[question.mission_id]
        if not may_answer(principal, mission):
            continue
        asked += 1
        if len(lines) < MAX_ITEMS:
            what = question.text if _shown(mission) else f"details in Z (data class {mission.data_class})"
            where = f" ({mission.title})" if _shown(mission) else ""
            lines.append(f"- Question to answer: {what}{where}\n  {base}/missions/{mission.id}")
    if not writes and not asked:
        return None
    more = writes + asked - len(lines)
    if more > 0:
        lines.append(f"- and {more} more in Z.")
    return {
        "user_id": user.id,
        "title": "Decisions waiting for you",
        "text": f"{writes} write(s) to approve and {asked} question(s) to answer.\n\n" + "\n".join(lines),
        "facts": [],
        "note": "Approving needs the Z app: links open the review, where step-up and approver rules apply.",
        "link": f"{base}/review",
        "link_label": "Open the review in Z",
    }


async def weekly(db: AsyncSession, state: AppState, user: User, until: datetime) -> dict[str, Any] | None:
    since = until - WEEK
    base = state.settings.public_url.rstrip("/")
    mine = (
        select(Mission)
        .where(Mission.created_by == user.id, Mission.eval_run_id.is_(None))
        .where(Mission.created_at >= since, Mission.created_at < until)
    )
    missions = list((await db.execute(mine)).scalars())
    given = int(
        (
            await db.execute(
                select(func.count())
                .select_from(ActionApproval)
                .where(ActionApproval.user_id == user.id, ActionApproval.at >= since, ActionApproval.at < until)
            )
        ).scalar_one()
    )
    if not missions and not given:
        return None
    by_status: dict[str, int] = {}
    for mission in missions:
        by_status[mission.status] = by_status.get(mission.status, 0) + 1
    writes: dict[str, int] = {}
    if missions:
        rows = await db.execute(
            select(ProposedAction.status, func.count())
            .where(ProposedAction.mission_id.in_([m.id for m in missions]))
            .group_by(ProposedAction.status)
        )
        writes = {str(status): int(count) for status, count in rows.all()}
    tokens = sum(m.tokens_in + m.tokens_out for m in missions)

    def counts(values: dict[str, int]) -> str:
        return ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(values.items())) or "none"

    return {
        "user_id": user.id,
        "title": "Your week in Z",
        "text": f"Missions you started from {since:%Y-%m-%d} to {until:%Y-%m-%d} (UTC).",
        "facts": [
            ("Missions", f"{len(missions)} ({counts(by_status)})" if missions else "0"),
            ("Writes proposed", f"{sum(writes.values())} ({counts(writes)})" if writes else "0"),
            ("Approvals you gave", str(given)),
            ("Tokens", f"{tokens:,}".replace(",", " ")),
        ],
        "note": "",
        "link": f"{base}/missions",
        "link_label": "Open your missions in Z",
    }


async def tick(state: AppState, at: datetime | None = None) -> dict[str, int]:
    """Enqueue the digests that are due; returns {digest name: mails enqueued} for the digests sent this time."""
    at = at or datetime.now(UTC)
    crons = schedules(state.config)
    if not crons:
        return {}
    async with state.sessions() as db, db.begin():
        if not (await notifications.settings(db))["smtp"]:
            return {}  # no SMTP server: digests are skipped, not queued
        if not (await db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY})).scalar():
            return {}  # another replica is on it
        row = await db.get(Setting, STATE_KEY, with_for_update=True)
        sent = dict(row.value) if row is not None else {}
        report: dict[str, int] = {}
        people: list[User] | None = None
        waiting: Pending | None = None
        for name, cron in crons.items():
            last = _parse(sent.get(name))
            if last is None:
                sent[name] = at.isoformat()  # first sight: start from now
                continue
            fire = latest_due(cron, last, at)
            if fire is None:
                continue
            people = people if people is not None else await recipients(db)
            count = 0
            if name == "daily_decisions" and waiting is None:
                waiting = await pending(db)
            for user in people:
                payload = (
                    await daily(db, state, user, waiting)
                    if waiting is not None and name == "daily_decisions"
                    else await weekly(db, state, user, fire)
                )
                if payload is None:
                    continue
                payload["digest"], payload["period"] = name, fire.isoformat()
                notifications.enqueue(db, "digest", payload=payload)
                count += 1
            sent[name] = fire.isoformat()
            report[name] = count
        if row is None:
            db.add(Setting(key=STATE_KEY, value=sent, updated_by="system"))
        elif row.value != sent:
            row.value, row.updated_by = sent, "system"
    return report


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
