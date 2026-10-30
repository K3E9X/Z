"""Pre-action checks (docs/04 §6.2): run when an agent proposes a write; the verdict is shown in Review.

- injection: the payload text through the judge (an agent steered by injected content may try to post a
  link, a secret or instructions somewhere). A blocked payload cannot be approved; flagged is a warning.
- data_class: the mission's class against the destination connector's class.
- fields: what a good ticket or comment needs (owner, deadline, a real description).
- duplicate: an open issue with the same title in the destination, or the same change already waiting
  for approval in Z.
Checks that need a tool call say "not checked" when the tool is unavailable; they never fail the proposal.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import DataClass, JudgeVerdict

from z_server import judge, judge_model
from z_server.db.models import Connector, Mission, Operation, ProposedAction
from z_server.state import AppState

log = logging.getLogger("z.prechecks")
MIN_DESCRIPTION = 20
TITLE_FIELD = {"jira.issues.create": "summary", "gitlab.issues.create": "title"}


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def fields(operation: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if operation in {"jira.issues.create", "gitlab.issues.create"}:
        description = str(payload.get("description") or "")
        out.append(
            _check(
                "description",
                len(description.strip()) >= MIN_DESCRIPTION,
                "has a description"
                if len(description.strip()) >= MIN_DESCRIPTION
                else "the description is empty or too short",
            )
        )
        out.append(
            _check("owner", bool(payload.get("assignee")), "assigned" if payload.get("assignee") else "no assignee")
        )
        out.append(
            _check(
                "deadline",
                bool(payload.get("due_date")),
                "has a due date" if payload.get("due_date") else "no due date",
            )
        )
    elif operation in {"jira.comments.create", "gitlab.notes.create"}:
        body = str(payload.get("body") or "").strip()
        out.append(_check("body", bool(body), "has text" if body else "the comment is empty"))
    return out


async def _search(db: AsyncSession, state: AppState, op_id: str, args: dict[str, Any]) -> Any:
    op = await db.get(Operation, op_id)
    connector = (
        await db.execute(select(Connector).where(Connector.name == op_id.split(".", 1)[0]))
    ).scalar_one_or_none()
    if op is None or connector is None or op.op_class != "read" or not connector.enabled or state.gateway is None:
        raise LookupError(f"{op_id} is not available")
    inv = await state.gateway.invoke(connector, op, args, "read", timeout_s=10)
    if not inv.ok:
        raise LookupError(f"{op_id} failed ({inv.error or inv.status})")
    return inv.body


async def duplicate(
    db: AsyncSession, state: AppState, mission: Mission, operation: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    field = TITLE_FIELD.get(operation)
    title = str(payload.get(field) or "") if field else ""
    if field is None or not title:
        return None
    same = [
        a
        for a in (
            await db.execute(
                select(ProposedAction).where(
                    ProposedAction.operation == operation,
                    ProposedAction.status.in_(["pending", "approved", "applying"]),
                )
            )
        ).scalars()
        if _norm(str(a.payload.get(field) or "")) == _norm(title) and a.payload.get("project") == payload.get("project")
    ]
    if same:
        return _check("duplicate", False, f"the same change is already waiting: {same[0].id}")
    quoted = title.replace("\\", " ").replace('"', " ")
    try:
        if operation == "jira.issues.create":
            body = await _search(
                db,
                state,
                "jira.issues.search",
                {
                    "jql": f'project = "{payload.get("project")}" AND summary ~ "{quoted}" AND statusCategory != Done',
                    "maxResults": 10,
                    "fields": "summary",
                },
            )
            hits = [
                i.get("key")
                for i in (body or {}).get("issues") or []
                if _norm(str((i.get("fields") or {}).get("summary", ""))) == _norm(title)
            ]
        else:
            body = await _search(
                db, state, "gitlab.issues.list", {"id": payload.get("project"), "search": title, "state": "opened"}
            )
            hits = [
                f"#{i.get('iid')}"
                for i in body or []
                if isinstance(i, dict) and _norm(str(i.get("title", ""))) == _norm(title)
            ]
    except LookupError as exc:
        return _check("duplicate", True, f"not checked: {exc}")
    if hits:
        return _check("duplicate", False, f"an open issue has the same title: {', '.join(map(str, hits[:3]))}")
    return _check("duplicate", True, "no open issue with this title")


async def run(
    db: AsyncSession,
    state: AppState,
    mission: Mission,
    connector: Connector,
    operation: str,
    payload: dict[str, Any],
    summary: str,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    text = "\n".join([summary, *_strings(payload)])
    verdict = await judge_model.review(state, text, judge.scan(text), owner=(mission.created_by, mission.team_id or ""))
    blocked = verdict.verdict == JudgeVerdict.blocked
    checks.append(
        _check(
            "injection",
            verdict.verdict == JudgeVerdict.clean,
            "clean"
            if verdict.verdict == JudgeVerdict.clean
            else f"{verdict.verdict.value}: {', '.join(verdict.categories)}",
        )
    )
    mission_class, dest_class = DataClass(mission.data_class), DataClass(connector.data_class)
    checks.append(
        _check(
            "data_class",
            mission_class.rank <= dest_class.rank,
            f"mission {mission_class.value}, destination {dest_class.value}"
            + (
                ""
                if mission_class.rank <= dest_class.rank
                else ": the payload may carry data above what the destination holds"
            ),
        )
    )
    checks += fields(operation, payload)
    dup = await duplicate(db, state, mission, operation, payload)
    if dup:
        checks.append(dup)
    overall = "block" if blocked else ("warn" if any(not c["ok"] for c in checks) else "ok")
    return {"verdict": overall, "checks": checks}
