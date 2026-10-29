"""Triggers -> tasks -> missions.

A trigger fires from a schedule, a signed webhook or Jira polling. Each firing creates a Task; with
`auto_start` and a playbook, a mission is also started as the trigger's owner, but only when the event
content is clean for the judge (webhook and ticket content is untrusted input, invariant 6) and the owner
may start missions. Otherwise the task waits on the Tasks board for a human.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import JudgeVerdict

from z_server import audit, judge
from z_server.auth import identity
from z_server.db.models import Playbook, Task, Trigger, User
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.missions import service
from z_server.missions.templates import TemplateFailure, render_value
from z_server.state import AppState

SKEW_S = 300
SOURCE = {
    "schedule": "schedule",
    "jira": "jira",
    "gitlab": "gitlab",
    "webhook": "webhook",
    "email": "email",
    "chat": "chat",
}


@dataclass
class Fired:
    task: Task
    mission_id: str | None
    note: str | None = None


def secret_of(state: AppState, trigger: Trigger) -> str | None:
    return state.sealer.open(trigger.secret_enc, f"trigger:{trigger.id}") if trigger.secret_enc else None


def verify(state: AppState, trigger: Trigger, headers: dict[str, str], body: bytes) -> None:
    """Raise unauthenticated unless the request carries the trigger's signature."""
    secret = secret_of(state, trigger)
    if not secret:
        raise Problem("unauthenticated", "this trigger has no webhook secret")
    scheme = (trigger.config or {}).get("signature") or ("gitlab_token" if trigger.kind == "gitlab" else "z_hmac")
    if scheme == "gitlab_token":
        if not hmac.compare_digest(headers.get("x-gitlab-token", ""), secret):
            raise Problem("unauthenticated", "bad X-Gitlab-Token")
        return
    if scheme == "hub_signature":
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(headers.get("x-hub-signature-256", ""), expected):
            raise Problem("unauthenticated", "bad X-Hub-Signature-256")
        return
    stamp = headers.get("x-z-timestamp", "")
    if not stamp.isdigit() or abs(time.time() - int(stamp)) > SKEW_S:
        raise Problem("unauthenticated", "X-Z-Timestamp missing or outside the 5 minute window")
    expected = "sha256=" + hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(headers.get("x-z-signature", ""), expected):
        raise Problem("unauthenticated", "bad X-Z-Signature")


def _path(value: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def gitlab_view(event: dict[str, Any]) -> dict[str, Any]:
    """Shortcuts for GitLab webhook filters: kind, action, labels (names), project."""
    attrs = event.get("object_attributes") or {}
    return {
        "kind": event.get("object_kind") or event.get("event_type"),
        "action": attrs.get("action"),
        "label": [lbl.get("title") for lbl in event.get("labels") or [] if isinstance(lbl, dict)],
        "project": (event.get("project") or {}).get("path_with_namespace"),
    }


def matches(filters: dict[str, Any], event: dict[str, Any], kind: str) -> bool:
    view = {**event, **(gitlab_view(event) if kind == "gitlab" else {})}
    for key, wanted in (filters or {}).items():
        actual = view.get(key) if key in view else _path(event, key)
        options = wanted if isinstance(wanted, list) else [wanted]
        values = actual if isinstance(actual, list) else [actual]
        if not any(str(v) == str(o) for v in values for o in options):
            return False
    return True


def _title(trigger: Trigger, event: dict[str, Any]) -> str:
    template = (trigger.config or {}).get("title")
    if template:
        try:
            return str(render_value(template, {"event": event}))[:300]
        except TemplateFailure:
            pass
    attrs = event.get("object_attributes") or {}
    return str(
        event.get("title") or attrs.get("title") or event.get("summary") or trigger.name or f"{trigger.kind} event"
    )[:300]


def _store(state: AppState, task_id: str, event: dict[str, Any]) -> str:
    folder = Path(state.settings.artifacts_dir) / "_tasks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{task_id}.json").write_text(json.dumps(event, ensure_ascii=False, default=str)[: 1024 * 1024])
    return f"task:{task_id}"


def load_body(state: AppState, task: Task) -> dict[str, Any] | None:
    if not task.body_ref or not task.body_ref.startswith("task:"):
        return None
    path = Path(state.settings.artifacts_dir) / "_tasks" / f"{task.id}.json"
    return dict(json.loads(path.read_text())) if path.is_file() else None


async def fire(
    db: AsyncSession, state: AppState, trigger: Trigger, event: dict[str, Any], external_ref: str | None, at: datetime
) -> Fired:
    if external_ref:
        seen = (
            (await db.execute(select(Task).where(Task.trigger_id == trigger.id, Task.external_ref == external_ref)))
            .scalars()
            .first()
        )
        if seen is not None:
            return Fired(seen, seen.mission_id, "already received")
    playbook = await db.get(Playbook, trigger.playbook_id) if trigger.playbook_id else None
    task = Task(
        id=new_id("tsk"),
        source=SOURCE.get(trigger.kind, "webhook"),
        external_ref=external_ref,
        title=_title(trigger, event),
        status="incoming",
        trigger_id=trigger.id,
        suggested_playbook=playbook.name if playbook else None,
        suggested_agent=(playbook.manifest.get("team") or [None])[0] if playbook else None,
    )
    db.add(task)
    await db.flush()
    task.body_ref = _store(state, task.id, event)
    trigger.last_fired_at = at
    await audit.record(
        db,
        audit.Actor(f"trigger:{trigger.id}"),
        "task.received",
        "task",
        task.id,
        after={"trigger": trigger.id, "kind": trigger.kind, "external_ref": external_ref},
    )
    if not (trigger.auto_start and playbook is not None):
        return Fired(task, None)
    verdict = judge.scan(json.dumps(event, ensure_ascii=False, default=str))
    if verdict.verdict != JudgeVerdict.clean:
        return Fired(
            task, None, f"not started: the judge {verdict.verdict.value} the event ({', '.join(verdict.categories)})"
        )
    owner = await db.get(User, trigger.owner_user_id)
    if owner is None or owner.status != "active":
        return Fired(task, None, "not started: the trigger owner is not active")
    principal = await identity.build_principal(db, owner, state.config_auth, kind="trigger")
    if not principal.can("missions:start", (principal.teams and sorted(principal.teams)[0]) or None):
        return Fired(task, None, "not started: the trigger owner cannot start missions")
    try:
        inputs = render_value(
            trigger.inputs or {}, {"event": event, "trigger": {"id": trigger.id, "name": trigger.name}}
        )
    except TemplateFailure as exc:
        return Fired(task, None, f"not started: inputs could not be rendered ({exc})")
    try:
        mission = await service.create(
            db,
            state,
            principal,
            service.MissionRequest(
                title=task.title,
                prompt=None,
                playbook=playbook,
                inputs=dict(inputs),
                plan=None,
                connectors=[],
                data_class=None,
                autonomy=None,
                plan_first=False,
                budget={},
                session_id=None,
                trigger_id=trigger.id,
            ),
        )
        await service.start(db, state, mission)
    except Problem as exc:
        return Fired(task, None, f"not started: {exc.detail or exc.kind}")
    task.status, task.mission_id = "started", mission.id
    await audit.record(
        db,
        audit.Actor(f"trigger:{trigger.id}"),
        "mission.started",
        "mission",
        mission.id,
        team=mission.team_id,
        runs_as=owner.username,
        after={"task": task.id, "playbook": playbook.name},
    )
    return Fired(task, mission.id)
