"""Decisions inside the Textual apps: approve all / review one by one / edit before approving / reject and
tell the agent why (docs/10 §1), and answering questions. The same flow serves `z top` (modal prompts) and
the interactive session (inline prompts) through a small `Presenter` interface."""

from __future__ import annotations

import json
from typing import Any, Protocol

from rich.console import Group, RenderableType
from rich.text import Text

from z_cli.client import AsyncApi
from z_cli.errors import CliError
from z_cli.render import action_header, action_panel, question_panel


class Presenter(Protocol):
    async def choose(self, body: RenderableType | None, options: list[str], *, danger: bool = False) -> int | None: ...

    async def ask_text(self, title: str, placeholder: str = "") -> str | None: ...

    async def edit_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None: ...

    def info(self, text: RenderableType) -> None: ...

    def error(self, exc: CliError) -> None: ...


def _ok(text: str) -> Text:
    t = Text("● ", style="z.ok")
    t.append(text)
    return t


async def approve_one(api: AsyncApi, ui: Presenter, action: dict[str, Any], comment: str | None = None) -> bool:
    if action.get("class") == "destructive":
        typed = await ui.ask_text(
            f"Destructive and not reversible. Type the operation id to approve: {action['operation']}",
            action["operation"],
        )
        if typed is None or typed.strip() != action["operation"]:
            ui.info(Text("Not approved: the operation id did not match.", style="z.wait"))
            return False
    try:
        result = await api.post(f"/actions/{action['id']}/approve", {"comment": comment} if comment else {})
    except CliError as exc:
        ui.error(exc)
        return False
    if result.get("status") == "approved":
        ui.info(_ok(f"Approved {action['operation']} · runs as {(result.get('runs_as') or {}).get('identity')}"))
    else:
        ui.info(
            _ok(
                f"Your approval of {action['id']} is recorded ({len(result.get('approvals') or [])}/{result.get('required_approvals')})"
            )
        )
    return True


async def reject_one(api: AsyncApi, ui: Presenter, action: dict[str, Any], reason: str | None = None) -> bool:
    if reason is None:
        reason = await ui.ask_text("Reject, and tell the agent why:", "e.g. duplicate of SEC-12")
    if not reason or not reason.strip():
        ui.info(Text("Not rejected: a reason is required.", style="z.wait"))
        return False
    try:
        await api.post(f"/actions/{action['id']}/reject", {"reason": reason})
    except CliError as exc:
        ui.error(exc)
        return False
    ui.info(_ok(f"Rejected {action['operation']}: {reason}"))
    return True


async def edit_one(api: AsyncApi, ui: Presenter, action: dict[str, Any]) -> dict[str, Any] | None:
    payload = await ui.edit_payload(action.get("payload") or {})
    if payload is None or payload == action.get("payload"):
        ui.info(Text("Nothing changed.", style="z.d"))
        return None
    try:
        new: dict[str, Any] = await api.post(f"/actions/{action['id']}/edit", {"payload": payload})
    except CliError as exc:
        ui.error(exc)
        return None
    ui.info(_ok(f"Edited: {new['id']} replaces {action['id']} (approvals start over)"))
    return new


async def review_action(api: AsyncApi, ui: Presenter, action: dict[str, Any]) -> str:
    """One action: approve / edit / reject / not now. Returns what happened."""
    while True:
        options = ["Approve", "Show the payload", "Edit before approving", "Reject, and tell the agent why", "Not now"]
        idx = await ui.choose(action_panel(action), options, danger=action.get("class") == "destructive")
        if idx == 0:
            return "approved" if await approve_one(api, ui, action) else "skipped"
        if idx == 1:
            ui.info(Text(json.dumps(action.get("payload"), indent=2, default=str)))
            continue
        if idx == 2:
            new = await edit_one(api, ui, action)
            if new is not None:
                action = new
            continue
        if idx == 3:
            if await reject_one(api, ui, action):
                return "rejected"
            continue
        return "skipped"


async def answer_question(api: AsyncApi, ui: Presenter, question: dict[str, Any]) -> str:
    options = list(question.get("options") or [])
    if options:
        idx = await ui.choose(question_panel(question, show_options=False), [*options, "Not now"])
        if idx is None or idx >= len(options):
            return "skipped"
        answer = options[idx]
    else:
        typed = await ui.ask_text(f"@{question.get('agent')} asks: {question.get('text')}", "your answer")
        if not typed:
            return "skipped"
        answer = typed
    try:
        await api.post(f"/questions/{question['id']}/answer", {"answer": answer})
    except CliError as exc:
        ui.error(exc)
        return "skipped"
    ui.info(_ok(f"Answered @{question.get('agent')}: {answer}"))
    return "answered"


def _group_body(actions: list[dict[str, Any]]) -> RenderableType:
    first = actions[0]
    head = Text()
    head.append("Approve writes", style="z.wait")
    ops = sorted({a["operation"] for a in actions})
    head.append(f"  {len(actions)} proposals · {', '.join(ops)}", style="z.d")
    rows: list[RenderableType] = [head, Text("")]
    for i, a in enumerate(actions, 1):
        t = Text(f"  {i}  ", style="z.d")
        t.append(a["summary"][:70])
        t.append(f"  {a['operation']}", style="z.err" if a["class"] == "destructive" else "z.d")
        t.append(f" · runs as {(a.get('runs_as') or {}).get('identity')}", style="z.d")
        rows.append(t)
    rows += [Text(""), action_header(first)]
    return Group(*rows)


async def decide(api: AsyncApi, ui: Presenter, *, mission_id: str | None = None) -> dict[str, int]:
    """Everything pending (for one mission, or all), in the order of docs/10 §1."""
    counts = {"approved": 0, "rejected": 0, "answered": 0, "skipped": 0}
    try:
        review = await api.get("/review", {"mission": mission_id, "limit": 200})
    except CliError as exc:
        ui.error(exc)
        return counts
    actions = [i["action"] for i in review["items"] if i["kind"] == "action"]
    questions = [i["question"] for i in review["items"] if i["kind"] == "question"]
    if not actions and not questions:
        ui.info(Text("Nothing waits for you.", style="z.d"))
        return counts
    if len(actions) == 1:
        counts[await review_action(api, ui, actions[0])] += 1
    elif actions:
        options = [
            f"Approve all ({len(actions)})",
            "Review one by one",
            "Edit before approving",
            "Reject, and tell the agent why",
            "Not now",
        ]
        idx = await ui.choose(_group_body(actions), options, danger=any(a["class"] == "destructive" for a in actions))
        if idx == 0:
            plain = [a for a in actions if a["class"] != "destructive"]
            if plain:
                try:
                    result = await api.post("/actions:batch-approve", {"action_ids": [a["id"] for a in plain]})
                except CliError as exc:
                    ui.error(exc)
                else:
                    ok = [r for r in result["results"] if r["ok"]]
                    counts["approved"] += len(ok)
                    ui.info(_ok(f"Approved {len(ok)} of {len(plain)}"))
                    for r in result["results"]:
                        if not r["ok"]:
                            err = r.get("error") or {}
                            ui.info(Text(f"{r['action_id']}: {err.get('detail') or err.get('title')}", style="z.err"))
            for a in actions:
                if a["class"] == "destructive":
                    counts["approved" if await approve_one(api, ui, a) else "skipped"] += 1
        elif idx == 1:
            for a in actions:
                counts[await review_action(api, ui, a)] += 1
        elif idx == 2:
            pick = await ui.choose(None, [f"{a['summary'][:60]} ({a['operation']})" for a in actions])
            if pick is not None:
                new = await edit_one(api, ui, actions[pick])
                if new is not None:
                    counts[await review_action(api, ui, new)] += 1
        elif idx == 3:
            reason = await ui.ask_text(f"Reject all {len(actions)}, and tell the agent why:", "e.g. out of scope")
            for a in actions:
                if await reject_one(api, ui, a, reason or ""):
                    counts["rejected"] += 1
                else:
                    break
        else:
            counts["skipped"] += len(actions)
    for q in questions:
        counts[await answer_question(api, ui, q)] += 1
    return counts
