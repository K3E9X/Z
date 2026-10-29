"""Notifications (docs/01 "Notifications", docs/06 §5 alerts, docs/12 §1 break-glass), ADR 0023.

What happens is written to a transactional outbox (`notification_outbox`) in the same transaction as the
change: an action waiting for approval, an agent question, a job needing attention, a mission ending, a budget
crossing its alert threshold, a connector going down, a break-glass sign-in. A worker delivers each row to the
admin-configured targets whose filters match (event kinds, teams), at least once, retrying with backoff.

Targets: Slack, Mattermost and Teams incoming webhooks, a generic webhook (JSON signed with HMAC-SHA256) and
email (SMTP). Webhook URLs carry their own token, so they are secret references like every credential. Content
(summaries, questions, mission titles) is sent only up to the target's `content_up_to` data class (default C1);
above it the message only says that something waits in Z, with the link. A notification never approves: links
open Z, where approval needs the Z session (and step-up, four eyes, two approvers as the rules say).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import smtplib
import ssl
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import DataClass, JobStatus, MissionStatus

from z_server.auth.identity import now
from z_server.db.models import Mission, NotificationOutbox, Setting
from z_server.errors import Problem
from z_server.gateway.secrets import SecretError, SecretResolver
from z_server.security import check_outbound_url

if TYPE_CHECKING:
    from z_server.state import AppState

log = logging.getLogger("z.notifications")
SETTINGS_KEY = "notifications"
STATE_KEY = "notifications_state"
KINDS = (
    "approval_requested",
    "question_asked",
    "needs_attention",
    "mission_succeeded",
    "mission_failed",
    "budget_alert",
    "connector_unhealthy",
    "breakglass_used",
)
TARGET_KINDS = ("slack", "mattermost", "teams", "webhook", "email")
MAX_ATTEMPTS = 6
BATCH = 50
SEND_TIMEOUT_S = 10.0
SECRET_BACKENDS = {"env", "file", "vault", "kubernetes"}
BUDGET_CHECK_S = 60.0
_budget_checked = 0.0


# ---------------------------------------------------------------- enqueue (same transaction as the change)


def enqueue(
    db: AsyncSession,
    kind: str,
    *,
    mission: Mission | None = None,
    event_seq: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown notification kind {kind}")
    db.add(
        NotificationOutbox(
            kind=kind,
            mission_id=mission.id if mission else None,
            event_seq=event_seq,
            team_id=mission.team_id if mission else None,
            data_class=mission.data_class if mission else DataClass.C0.value,
            payload=payload or {},
            attempts=0,
            next_at=now(),
            done=False,
            delivered=[],
        )
    )


def kind_for_event(event_type: str, payload: dict[str, Any]) -> str | None:
    """The notification an event raises, if any (called by events.append)."""
    if event_type == "action_proposed":
        return "approval_requested"
    if event_type == "question":
        return "question_asked"
    if event_type == "status":
        to = str(payload.get("to") or "")
        if payload.get("entity") == "job" and to == JobStatus.needs_attention.value:
            return "needs_attention"
        if payload.get("entity") == "mission" and to in MissionStatus.__members__ and MissionStatus(to).terminal:
            return "mission_succeeded" if to == MissionStatus.succeeded.value else "mission_failed"
    return None


# ---------------------------------------------------------------- settings


async def settings(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(Setting, SETTINGS_KEY)
    value = dict(row.value) if row is not None else {}
    return {"targets": list(value.get("targets") or []), "smtp": value.get("smtp")}


async def delivery_state(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(Setting, STATE_KEY)
    return dict(row.value) if row is not None else {}


def _ref(value: Any, what: str) -> None:
    if value is not None and (
        not isinstance(value, str) or ":" not in value or value.split(":", 1)[0] not in SECRET_BACKENDS
    ):
        raise Problem("validation", f"{what} is a secret reference (env:, file:, vault:, kubernetes:), never a value")


def validate(value: dict[str, Any]) -> dict[str, Any]:
    """Checks a NotificationSettings body; returns it without read-only status fields."""
    targets, ids = [], set()
    for raw in value.get("targets") or []:
        target = {k: v for k, v in dict(raw).items() if k != "status"}
        tid, kind = str(target.get("id") or ""), target.get("kind")
        if not tid or tid in ids:
            raise Problem("validation", "every notification target has a unique id")
        ids.add(tid)
        if kind not in TARGET_KINDS:
            raise Problem("validation", f"target {tid}: kind is one of {', '.join(TARGET_KINDS)}")
        unknown = set(target.get("events") or []) - set(KINDS)
        if unknown or not target.get("events"):
            raise Problem("validation", f"target {tid}: events are some of {', '.join(KINDS)}")
        if target.get("content_up_to", "C1") not in {c.value for c in DataClass}:
            raise Problem("validation", f"target {tid}: content_up_to is a data class (C0 to C3)")
        _ref(target.get("url_ref"), f"target {tid}: url_ref")
        _ref(target.get("secret_ref"), f"target {tid}: secret_ref")
        if kind == "email":
            to = target.get("to") or []
            if not to or not all(isinstance(a, str) and "@" in a and "\n" not in a for a in to):
                raise Problem("validation", f"target {tid}: email targets list recipient addresses in `to`")
        elif not target.get("url_ref"):
            raise Problem("validation", f"target {tid}: {kind} targets need url_ref (the webhook URL, as a reference)")
        targets.append(target)
    smtp = value.get("smtp")
    if smtp:
        _ref(smtp.get("password_ref"), "smtp.password_ref")
        if not smtp.get("host") or not smtp.get("from"):
            raise Problem("validation", "smtp needs host and from")
    if any(t["kind"] == "email" for t in targets) and not smtp:
        raise Problem("validation", "email targets need the smtp settings")
    return {"targets": targets, "smtp": smtp or None}


# ---------------------------------------------------------------- messages


def _shown(target: dict[str, Any], data_class: str) -> bool:
    try:
        return DataClass(data_class).rank <= DataClass(str(target.get("content_up_to") or "C1")).rank
    except ValueError:
        return False


TITLES = {
    "approval_requested": "Approval needed",
    "question_asked": "An agent asks a question",
    "needs_attention": "A job needs attention",
    "mission_succeeded": "Mission succeeded",
    "mission_failed": "Mission ended without succeeding",
    "budget_alert": "Budget alert",
    "connector_unhealthy": "Connector unreachable",
    "breakglass_used": "Break-glass sign-in",
}


async def compose(db: AsyncSession, state: AppState, row: NotificationOutbox, target: dict[str, Any]) -> dict[str, Any]:
    """{title, text, facts, note, link, link_label}, with content only up to the target's data class."""
    from z_server.db.models import Event

    base = state.settings.public_url.rstrip("/")
    p = dict(row.payload or {})
    shown = _shown(target, row.data_class)
    hidden = f"Details are in Z (data class {row.data_class})."
    msg: dict[str, Any] = {"title": TITLES[row.kind], "text": "", "facts": [], "note": "", "link": base}
    msg["link_label"] = "Open in Z"
    if row.mission_id:
        mission = await db.get(Mission, row.mission_id)
        event = await db.get(Event, (row.mission_id, row.event_seq)) if row.event_seq is not None else None
        ep = dict(event.payload or {}) if event is not None else {}
        title = mission.title if mission is not None else row.mission_id
        msg["link"] = f"{base}/missions/{row.mission_id}"
        if row.kind == "approval_requested":
            msg["text"] = str(ep.get("summary") or "") if shown else hidden
            msg["facts"] = [("Operation", str(ep.get("operation"))), ("Class", str(ep.get("class")))]
            destructive = ep.get("class") == "destructive"
            msg["note"] = "Destructive: two approvers, in the Z app only." if destructive else "Approve or reject in Z."
            msg["link"], msg["link_label"] = f"{base}/review?action={ep.get('action_id')}", "Review in Z"
        elif row.kind == "question_asked":
            msg["text"] = str(ep.get("text") or "") if shown else hidden
            msg["link_label"] = "Answer in Z"
        else:
            msg["text"] = title if shown else hidden
            if row.kind == "mission_failed" and ep.get("to"):
                msg["facts"].append(("Status", str(ep["to"]).replace("_", " ")))
            if ep.get("reason") and shown:
                msg["facts"].append(("Reason", str(ep["reason"])))
        if shown and row.kind in {"approval_requested", "question_asked"}:
            msg["facts"].insert(0, ("Mission", title))
    elif row.kind == "budget_alert":
        msg["text"] = f"{p.get('scope')} has used {p.get('used')} of {p.get('limit')} tokens this {p.get('period')}."
        msg["facts"] = [("Threshold", f"{int(float(p.get('alert_at') or 0) * 100)} %")]
        msg["link"] = f"{base}/usage"
    elif row.kind == "connector_unhealthy":
        msg["text"] = f"{p.get('connector')} is {p.get('status')}: {p.get('detail') or ''}".strip()
        msg["link"] = f"{base}/connectors"
    elif row.kind == "breakglass_used":
        msg["text"] = f"The break-glass account signed in from {p.get('ip') or 'an unknown address'}."
        msg["note"] = "Every action of this session is audited."
        msg["link"] = f"{base}/history?actor=z-breakglass"
    return msg


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def plain(msg: dict[str, Any], style: str) -> str:
    facts = " · ".join(f"{k}: {v}" for k, v in msg["facts"])
    if style == "slack":
        link = f"<{msg['link']}|{msg['link_label']}>"
        lines = [f"*{_escape(msg['title'])}*", _escape(msg["text"]), _escape(facts), msg["note"], link]
    elif style == "markdown":
        lines = [f"**{msg['title']}**", msg["text"], facts, msg["note"], f"[{msg['link_label']}]({msg['link']})"]
    else:
        lines = [msg["title"], msg["text"], facts, msg["note"], f"{msg['link_label']}: {msg['link']}"]
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------- delivery


class DeliveryError(Exception):
    pass


async def _url(resolver: SecretResolver, target: dict[str, Any]) -> str:
    try:
        url = await resolver.resolve(str(target["url_ref"]))
    except (SecretError, KeyError) as exc:
        raise DeliveryError("the webhook URL reference is unavailable") from exc
    if not url.startswith("https://") and not url.startswith("http://"):
        raise DeliveryError("the webhook URL is not http(s)")
    try:
        await check_outbound_url(url)
    except Problem as exc:
        raise DeliveryError(exc.detail or "the webhook URL is not allowed") from exc
    return url


async def _post(client: httpx.AsyncClient, url: str, body: bytes, headers: dict[str, str]) -> None:
    try:
        resp = await client.post(
            url,
            content=body,
            headers={"Content-Type": "application/json", **headers},
            timeout=SEND_TIMEOUT_S,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise DeliveryError(f"unreachable ({type(exc).__name__})") from exc
    if not 200 <= resp.status_code < 300:
        raise DeliveryError(f"HTTP {resp.status_code}")


def _send_mail(smtp: dict[str, Any], password: str | None, to: list[str], subject: str, text: str) -> None:
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = str(smtp["from"]), ", ".join(to), subject
    mail.set_content(text)
    port = int(smtp.get("port") or 587)
    context = ssl.create_default_context()
    if port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(str(smtp["host"]), port, timeout=SEND_TIMEOUT_S, context=context)
    else:
        server = smtplib.SMTP(str(smtp["host"]), port, timeout=SEND_TIMEOUT_S)
    with server:
        if port != 465 and smtp.get("starttls", True):
            server.starttls(context=context)
        if smtp.get("username"):
            server.login(str(smtp["username"]), password or "")
        server.send_message(mail)


async def deliver(
    state: AppState, resolver: SecretResolver, target: dict[str, Any], smtp: dict[str, Any] | None, msg: dict[str, Any]
) -> None:
    kind = target["kind"]
    if kind == "email":
        if not smtp:
            raise DeliveryError("no smtp settings")
        password = None
        if smtp.get("password_ref"):
            try:
                password = await resolver.resolve(str(smtp["password_ref"]))
            except SecretError as exc:
                raise DeliveryError("the smtp password reference is unavailable") from exc
        try:
            await asyncio.to_thread(
                _send_mail, smtp, password, list(target["to"]), f"[Z] {msg['title']}", plain(msg, "text")
            )
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryError(f"smtp: {type(exc).__name__}") from exc
        return
    url = await _url(resolver, target)
    if kind == "slack":
        body: Any = {"text": plain(msg, "slack")}
    elif kind == "mattermost":
        body = {"text": plain(msg, "markdown")}
    elif kind == "teams":
        from z_server.channel_threads import adaptive_card

        body = {"type": "message", "attachments": [adaptive_card(msg)]}
    else:
        body = {k: msg[k] for k in ("title", "text", "note", "link")} | {
            "facts": [{"name": k, "value": v} for k, v in msg["facts"]],
            "kind": msg.get("kind"),
            "id": msg.get("id"),
            "ts": msg.get("ts"),
        }
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    headers: dict[str, str] = {}
    if kind == "webhook" and target.get("secret_ref"):
        try:
            secret = await resolver.resolve(str(target["secret_ref"]))
        except SecretError as exc:
            raise DeliveryError("the signing secret reference is unavailable") from exc
        stamp = str(int(time.time()))
        digest = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        headers = {"X-Z-Timestamp": stamp, "X-Z-Signature": f"sha256={digest}"}
    await _post(state.http, url, raw, headers)


def matches(target: dict[str, Any], row: NotificationOutbox) -> bool:
    if not target.get("enabled", True) or row.kind not in (target.get("events") or []):
        return False
    teams = target.get("teams") or []
    return not teams or row.team_id in teams or row.mission_id is None


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(3600, 30 * 2 ** max(0, attempts - 1)))


async def tick(state: AppState) -> dict[str, int]:
    resolver = state.gateway.resolver if state.gateway else SecretResolver(state.http)
    report = {"sent": 0, "failed": 0, "rows": 0}
    async with state.sessions() as db, db.begin():
        config = await settings(db)
        status = await delivery_state(db)
        rows = list(
            (
                await db.execute(
                    select(NotificationOutbox)
                    .where(NotificationOutbox.done.is_(False), NotificationOutbox.next_at <= now())
                    .order_by(NotificationOutbox.id)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        for row in rows:
            report["rows"] += 1
            delivered = set(row.delivered or [])
            failed = False
            for target in config["targets"]:
                if target["id"] in delivered or not matches(target, row):
                    continue
                msg = await compose(db, state, row, target)
                msg.update({"kind": row.kind, "id": f"ntf_{row.id}", "ts": row.created_at.isoformat()})
                entry = dict(status.get(target["id"]) or {})
                try:
                    await deliver(state, resolver, target, config["smtp"], msg)
                except DeliveryError as exc:
                    failed = True
                    report["failed"] += 1
                    entry.update({"last_error": str(exc)[:300], "last_error_at": now().isoformat()})
                    log.warning("notification %s to %s failed: %s", row.id, target["id"], exc)
                else:
                    delivered.add(target["id"])
                    report["sent"] += 1
                    entry.update({"last_sent_at": now().isoformat(), "last_error": None})
                status[target["id"]] = entry
            row.delivered = sorted(delivered)
            row.attempts += 1
            if not failed or row.attempts >= MAX_ATTEMPTS:
                row.done = True
            else:
                row.next_at = now() + backoff(row.attempts)
        await _save_state(db, status)
        global _budget_checked
        if time.monotonic() - _budget_checked >= BUDGET_CHECK_S:
            _budget_checked = time.monotonic()
            await _budget_alerts(db)
    return report


async def _save_state(db: AsyncSession, value: dict[str, Any]) -> None:
    row = await db.get(Setting, STATE_KEY)
    if row is None:
        db.add(Setting(key=STATE_KEY, value=value, updated_by="system"))
    elif row.value != value:
        row.value, row.updated_by = value, "system"


def _period_key(period: str, at: datetime) -> str:
    if period == "day":
        return at.strftime("%Y-%m-%d")
    if period == "week":
        year, week, _ = at.isocalendar()
        return f"{year}-W{week:02d}"
    return at.strftime("%Y-%m")


async def _budget_alerts(db: AsyncSession) -> None:
    """Once per budget and period, when usage crosses `alert_at` of the limit."""
    from z_server import usage
    from z_server.db.models import Budget

    at = now()
    row = await db.get(Setting, "budget_alerts")
    sent = dict(row.value) if row is not None else {}
    changed = False
    for budget in (await db.execute(select(Budget).where(Budget.period != "mission"))).scalars():
        key = f"{budget.id}:{_period_key(budget.period, at)}"
        if key in sent or budget.limit <= 0:
            continue
        spent = await usage.used(db, budget.scope, budget.period, at)
        if spent >= budget.limit * budget.alert_at:
            enqueue(
                db,
                "budget_alert",
                payload={
                    "scope": budget.scope,
                    "used": spent,
                    "limit": budget.limit,
                    "period": budget.period,
                    "alert_at": budget.alert_at,
                },
            )
            sent[key], changed = at.isoformat(), True
    if changed:
        if row is None:
            db.add(Setting(key="budget_alerts", value=sent, updated_by="system"))
        else:
            row.value, row.updated_by = sent, "system"


async def send_test(state: AppState, db: AsyncSession, target_id: str) -> None:
    config = await settings(db)
    target = next((t for t in config["targets"] if t["id"] == target_id), None)
    if target is None:
        raise Problem("not-found", "no such notification target")
    resolver = state.gateway.resolver if state.gateway else SecretResolver(state.http)
    msg = {
        "title": "Z test notification",
        "text": "This target receives Z notifications.",
        "facts": [("Events", ", ".join(target.get("events") or []))],
        "note": "",
        "link": state.settings.public_url,
        "link_label": "Open Z",
        "kind": "test",
        "id": "ntf_test",
        "ts": now().isoformat(),
    }
    try:
        await deliver(state, resolver, target, config["smtp"], msg)
    except DeliveryError as exc:
        raise Problem("unavailable", f"the test notification failed: {exc}") from exc
