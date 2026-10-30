"""Deciding on proposed writes and answering questions (invariant 4: nothing is written without a human).

Before an approval the CLI always shows the preview, the runs-as identity, reversibility and the judge
verdict. Destructive actions need the operation id typed back (or `--confirm-destructive <operation>`);
the server still requires two distinct approvers with a fresh step-up.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from rich.text import Text

from z_cli import output
from z_cli.client import Api
from z_cli.commands._common import ask, confirm
from z_cli.errors import CliError, UsageError
from z_cli.render import action_panel, age, question_panel


def get_action(api: Api, action_id: str) -> dict[str, Any]:
    action: dict[str, Any] = api.get(f"/actions/{action_id}")
    return action


def check_pending(action: dict[str, Any]) -> None:
    if action.get("status") != "pending":
        raise CliError(
            f"Action {action['id']} is {action.get('status')}; only pending actions can be decided.",
            hint=f"It was superseded by {action['superseded_by']}." if action.get("superseded_by") else None,
        )


def confirm_approval(action: dict[str, Any], *, yes: bool, confirm_destructive: str | None) -> None:
    """Raises unless the human confirmed. Destructive actions are never confirmed by --yes alone."""
    if action.get("class") == "destructive":
        operation = str(action.get("operation"))
        if confirm_destructive is not None:
            if confirm_destructive != operation:
                raise UsageError(f"--confirm-destructive must be the operation id {operation!r}")
            return
        if not sys.stdin.isatty():
            raise UsageError(
                f"{action['id']} is a destructive action: it needs an explicit confirmation.",
                hint=f"Run it in a terminal, or pass --confirm-destructive {operation}",
            )
        output.err().print(
            Text("This action is destructive and cannot be undone. Type the operation id to approve it.", style="z.err")
        )
        typed = ask(f"Operation id ({operation})")
        if typed.strip() != operation:
            raise CliError("Not approved: the operation id did not match.")
        return
    if not confirm(f"Approve {action.get('operation')} as {(action.get('runs_as') or {}).get('identity')}?", yes=yes):
        raise CliError("Not approved.")


def approve(
    api: Api,
    action_id: str,
    *,
    comment: str | None = None,
    exclude: list[str] | None = None,
    yes: bool = False,
    confirm_destructive: str | None = None,
    show: bool = True,
) -> dict[str, Any]:
    action = get_action(api, action_id)
    check_pending(action)
    if show:
        output.err().print(action_panel(action))
    confirm_approval(action, yes=yes, confirm_destructive=confirm_destructive)
    body: dict[str, Any] = {}
    if comment:
        body["comment"] = comment
    if exclude:
        body["exclude"] = exclude
    result: dict[str, Any] = api.post(f"/actions/{action_id}/approve", body)
    return result


def reject(api: Api, action_id: str, reason: str) -> dict[str, Any]:
    if not reason.strip():
        raise UsageError("a reason is required: it tells the agent why")
    result: dict[str, Any] = api.post(f"/actions/{action_id}/reject", {"reason": reason})
    return result


def editor_command() -> list[str]:
    from z_cli import state

    configured = state.get().setting("editor")
    cmd = configured or os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    return shlex.split(cmd)


def edit_in_editor(payload: dict[str, Any]) -> dict[str, Any]:
    if not sys.stdin.isatty():
        raise UsageError("editing needs a terminal", hint="Pass the new payload with --payload-file or --set.")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        path = Path(fh.name)
    try:
        subprocess.run([*editor_command(), str(path)], check=False)  # noqa: S603 - the user's own editor
        text = path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise UsageError(f"the edited payload is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError("the payload must be a JSON object")
    return data


def apply_sets(payload: dict[str, Any], sets: list[str]) -> dict[str, Any]:
    """`--set summary="New title"`, `--set labels:=["a","b"]`, `--set fields.priority=High`."""
    out: dict[str, Any] = json.loads(json.dumps(payload))
    for item in sets:
        eq = item.find("=")
        if eq <= 0:
            raise UsageError(f"--set {item!r}: use path=value or path:=<json>")
        raw_json = item[eq - 1] == ":"
        path = item[: eq - 1] if raw_json else item[:eq]
        raw = item[eq + 1 :]
        try:
            value = json.loads(raw) if raw_json else raw
        except ValueError as exc:
            raise UsageError(f"--set {path}: not valid JSON ({exc})") from exc
        target = out
        keys = path.split(".")
        for key in keys[:-1]:
            target = target.setdefault(key, {})
            if not isinstance(target, dict):
                raise UsageError(f"--set {path}: {key} is not an object")
        target[keys[-1]] = value
    return out


def edit(api: Api, action_id: str, payload: dict[str, Any], comment: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"payload": payload}
    if comment:
        body["comment"] = comment
    result: dict[str, Any] = api.post(f"/actions/{action_id}/edit", body)
    return result


def answer(api: Api, question_id: str, text: str) -> dict[str, Any]:
    if not text.strip():
        raise UsageError("the answer is empty")
    result: dict[str, Any] = api.post(f"/questions/{question_id}/answer", {"answer": text})
    return result


# ---------------------------------------------------------------- one by one (z review, run --wait)

ACTION_CHOICES = [
    "Approve",
    "Review the payload (JSON)",
    "Edit before approving",
    "Reject, and tell the agent why",
    "Skip",
]


def _pick(n: int, prompt: str = "Choice") -> int:
    while True:
        raw = ask(f"{prompt} [1-{n}]", default="1")
        if raw.isdigit() and 1 <= int(raw) <= n:
            return int(raw)
        output.note(f"type a number from 1 to {n}")


def decide_action(api: Api, action: dict[str, Any]) -> str:
    """Interactive decision on one action. Returns what happened: approved, rejected, edited, skipped."""
    while True:
        output.err().print(action_panel(action, options=ACTION_CHOICES))
        choice = _pick(len(ACTION_CHOICES))
        if choice == 1:
            try:
                confirm_approval(action, yes=action.get("class") != "destructive", confirm_destructive=None)
                comment = ask("Comment (optional)", default="")
                result = api.post(f"/actions/{action['id']}/approve", {"comment": comment} if comment else {})
            except CliError as exc:
                output.report_error(exc)
                return "skipped"
            done = result.get("status") == "approved"
            output.success(
                f"{action['id']} approved"
                if done
                else f"{action['id']}: your approval is recorded ({len(result.get('approvals') or [])}/{result.get('required_approvals')})"
            )
            return "approved"
        if choice == 2:
            output.err().print_json(json.dumps(action.get("payload"), default=str))
            continue
        if choice == 3:
            try:
                new = edit(api, action["id"], edit_in_editor(action.get("payload") or {}))
            except CliError as exc:
                output.report_error(exc)
                continue
            output.success(f"Edited: the new proposal is {new['id']} (approvals start over)")
            action = new
            continue
        if choice == 4:
            reason = ask("Why? (the agent reads this)")
            try:
                reject(api, action["id"], reason)
            except CliError as exc:
                output.report_error(exc)
                continue
            output.success(f"{action['id']} rejected")
            return "rejected"
        return "skipped"


def decide_question(api: Api, question: dict[str, Any]) -> str:
    output.err().print(question_panel(question))
    options = list(question.get("options") or [])
    if options:
        idx = _pick(len(options) + 1, f"Answer (or {len(options) + 1} to skip)")
        if idx == len(options) + 1:
            return "skipped"
        text = options[idx - 1]
    else:
        text = ask("Answer (empty to skip)", default="")
        if not text:
            return "skipped"
    try:
        answer(api, question["id"], text)
    except CliError as exc:
        output.report_error(exc)
        return "skipped"
    output.success(f"Answered {question['id']}")
    return "answered"


def one_by_one(api: Api, *, mission: str | None = None, kind: str | None = None) -> dict[str, int]:
    counts = {"approved": 0, "rejected": 0, "answered": 0, "skipped": 0, "edited": 0}
    review = api.get("/review", {"mission": mission, "kind": kind, "limit": 200})
    items = [i for i in review.get("items", []) if i["kind"] in {"action", "question"}]
    if not items:
        output.note("Nothing waits for you.")
        return counts
    output.note(f"{len(items)} pending decision(s).")
    for n, item in enumerate(items, 1):
        output.err().print(
            Text(f"\n[{n}/{len(items)}] {item['mission_title']} · waiting {age(item['age_s'])}", style="z.d")
        )
        result = (
            decide_action(api, item["action"]) if item["kind"] == "action" else decide_question(api, item["question"])
        )
        counts[result] = counts.get(result, 0) + 1
    return counts
