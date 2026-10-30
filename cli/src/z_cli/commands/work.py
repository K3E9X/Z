"""Jobs and artifacts; the review inbox (approve, reject, edit, answer)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Group
from rich.text import Text

from z_cli import output, review, state
from z_cli.commands._common import JsonOpt, LimitOpt, YesOpt, command, guarded, interactive
from z_cli.errors import UsageError
from z_cli.render import action_panel, age, question_panel, tokens, when
from z_cli.theme import state_style

jobs_app = typer.Typer(help="Jobs (one agent run on one input) and their artifacts.", no_args_is_help=True)
review_app = typer.Typer(
    help="Pending writes and questions. Without a subcommand: decide them one by one.", invoke_without_command=True
)
top_level = typer.Typer()


# ---------------------------------------------------------------- jobs


@command(jobs_app, "show")
def job_show(job_id: str, as_json: JsonOpt = False) -> None:
    """Show a job: agent, status, attempt, tokens, error, output."""
    job = state.get().api().get(f"/jobs/{job_id}")
    if as_json:
        output.print_json(job)
        return
    error = job.get("error") or {}
    rows = output.kv(
        [
            ("job", Text(job["id"], style="z.b")),
            ("mission", job["mission_id"]),
            ("step", job["step_id"] + (f" [{job['item_key']}]" if job.get("item_key") else "")),
            ("agent", f"@{job['agent']} v{job['agent_version']} · {job['model']}"),
            (
                "status",
                Text(
                    job["status"]
                    + (f" ({job['needs_attention_reason']})" if job.get("needs_attention_reason") else ""),
                    style=state_style(job["status"]),
                ),
            ),
            (
                "attempt",
                str(job["attempt"])
                + (f" · resumes from step {job['resume_from_step']}" if job.get("resume_from_step") else ""),
            ),
            ("steps", str(job["steps_done"])),
            ("tokens", f"{tokens(job['tokens_in'])} in · {tokens(job['tokens_out'])} out"),
            ("started", when(job.get("started_at")) or None),
            ("ended", when(job.get("ended_at")) or None),
            ("parent", job.get("parent_job_id")),
            ("error", Text(f"{error.get('class')}: {error.get('message')}", style="z.err") if error else None),
        ]
    )
    parts: list[Any] = [rows]
    if job.get("output") is not None:
        parts += [Text(""), Text("output", style="z.d"), Text(json.dumps(job["output"], indent=2, default=str))]
    output.out().print(Group(*parts))


@command(jobs_app, "retry")
def job_retry(
    job_id: str,
    reason: Annotated[str | None, typer.Option("--reason", "-r", help="Why (kept in the audit log).")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Retry a failed, cancelled, timed-out or blocked job from its last checkpoint."""
    job = state.get().api().post(f"/jobs/{job_id}/retry", {"reason": reason} if reason else {})
    if as_json:
        output.print_json(job)
        return
    output.success(f"Job {job['id']} queued again (attempt {job['attempt']})")


@command(jobs_app, "artifacts")
def job_artifacts(job_id: str, as_json: JsonOpt = False) -> None:
    """List the files a job produced."""
    items = state.get().api().get(f"/jobs/{job_id}/artifacts")
    output.emit(
        items,
        as_json,
        lambda data: (
            output.table(
                ["artifact", "kind", "name", "type", "size", "class", "created"],
                [
                    [
                        a["id"],
                        a["kind"],
                        a["name"],
                        a["content_type"],
                        str(a["size"]),
                        a["data_class"],
                        when(a["created_at"]),
                    ]
                    for a in data
                ],
            )
            if data
            else Text("No artifacts.", style="z.d")
        ),
    )


@command(jobs_app, "download")
def job_download(
    artifact_id: Annotated[str, typer.Argument(help="Artifact id (art_…), from `z jobs artifacts`.")],
    dest: Annotated[
        Path | None, typer.Option("--output", "-o", help="File or directory (default: its name, here). '-' = stdout.")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Download an artifact (C2+ downloads are audited)."""
    api = state.get().api()
    meta = api.get(f"/artifacts/{artifact_id}", {"meta": "true"})
    response = api.request("GET", f"/artifacts/{artifact_id}")
    if dest is not None and str(dest) == "-":
        sys.stdout.buffer.write(response.content)
        sys.stdout.flush()
        return
    target = dest or Path(Path(meta["name"]).name)
    if target.is_dir():
        target = target / Path(meta["name"]).name
    if target.exists() and not force:
        raise UsageError(f"{target} exists", hint="Pass --force to overwrite, or -o to choose another path.")
    target.write_bytes(response.content)
    import hashlib

    digest = hashlib.sha256(response.content).hexdigest()
    if digest != meta.get("sha256"):
        target.unlink(missing_ok=True)
        raise UsageError(f"checksum mismatch for {artifact_id}: the download was corrupted; nothing was kept")
    if as_json:
        output.print_json({**meta, "path": str(target)})
        return
    output.success(f"Saved {target} ({meta['size']} bytes, sha256 ok, {meta['data_class']})")


# ---------------------------------------------------------------- review


def review_rows(data: dict[str, Any]) -> Any:
    rows = []
    for item in data["items"]:
        if item["kind"] == "action":
            a = item["action"]
            what = Text(a["operation"], style="z.err" if a["class"] == "destructive" else "z.wait")
            rows.append(
                [
                    Text(a["id"], style="z.d"),
                    what,
                    a["summary"][:60],
                    f"@{a['agent']}",
                    str((a.get("runs_as") or {}).get("identity", "")),
                    f"{len(a['approvals'])}/{a['required_approvals']}",
                    item["mission_title"][:40],
                    age(item["age_s"]),
                ]
            )
        elif item["kind"] == "question":
            q = item["question"]
            rows.append(
                [
                    Text(q["id"], style="z.d"),
                    Text("question", style="z.wait"),
                    q["text"][:60],
                    f"@{q['agent']}",
                    "",
                    " / ".join(q.get("options") or [])[:20],
                    item["mission_title"][:40],
                    age(item["age_s"]),
                ]
            )
        else:
            s = item["suggestion"]
            rows.append(
                [
                    Text(s["id"], style="z.d"),
                    Text("suggestion", style="z.run"),
                    s["title"][:60],
                    f"@{s['agent']}",
                    "",
                    "",
                    item["mission_title"][:40],
                    age(item["age_s"]),
                ]
            )
    counts = data["counts"]
    head = Text(
        f"{counts['all']} pending · {counts['writes']} writes, {counts['questions']} questions, "
        f"{counts['suggestions']} suggestions",
        style="z.wait" if counts["all"] else "z.d",
    )
    if not rows:
        return head
    return Group(
        head, Text(""), output.table(["id", "what", "summary", "agent", "runs as", "approvals", "mission", "age"], rows)
    )


KindOpt = Annotated[str | None, typer.Option("--kind", "-k", help="writes, questions or suggestions.")]
MissionOpt = Annotated[str | None, typer.Option("--mission", "-m", help="Only this mission.")]


@review_app.callback()
def review_default(ctx: typer.Context, mission: MissionOpt = None, kind: KindOpt = None) -> None:
    """Without a subcommand: go through pending writes and questions one by one."""
    if ctx.invoked_subcommand is not None:
        return
    guarded(_one_by_one, mission, kind)


def _one_by_one(mission: str | None, kind: str | None) -> None:
    if not interactive():
        raise UsageError(
            "`z review` decides interactively and needs a terminal.", hint="Use `z review list --json` in scripts."
        )
    counts = review.one_by_one(state.get().api(), mission=mission, kind=kind)
    done = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
    if done:
        output.note(done)


@command(review_app, "list")
def review_list(
    mission: MissionOpt = None, kind: KindOpt = None, limit: LimitOpt = 50, as_json: JsonOpt = False
) -> None:
    """List what waits for a decision."""
    data = state.get().api().get("/review", {"mission": mission, "kind": kind, "limit": limit})
    output.emit(data, as_json, review_rows)


@command(review_app, "show")
def review_show(
    item_id: Annotated[str, typer.Argument(help="Action (act_…) or question (q…) id.")], as_json: JsonOpt = False
) -> None:
    """Show one action (preview, runs-as, reversibility, judge) or question."""
    api = state.get().api()
    if item_id.startswith("act"):
        action = review.get_action(api, item_id)
        output.emit(action, as_json, lambda a: action_panel(a))
        return
    data = api.get("/review", {"kind": "questions", "limit": 200})
    for item in data["items"]:
        if item.get("question", {}).get("id") == item_id:
            output.emit(item["question"], as_json, lambda q: question_panel(q))
            return
    raise UsageError(f"{item_id} is not a pending question or an action id")


CommentOpt = Annotated[str | None, typer.Option("--comment", "-m", help="Note kept with your approval.")]
ConfirmOpt = Annotated[
    str | None,
    typer.Option(
        "--confirm-destructive",
        metavar="OPERATION",
        help="Approve a destructive action without a prompt: type its operation id.",
    ),
]
ExcludeOpt = Annotated[
    list[str] | None, typer.Option("--exclude", help="Item id to drop from a batch payload (repeat).")
]


def _approve(
    action_id: str,
    comment: str | None,
    exclude: list[str] | None,
    yes: bool,
    confirm_destructive: str | None,
    as_json: bool,
) -> None:
    result = review.approve(
        state.get().api(),
        action_id,
        comment=comment,
        exclude=exclude,
        yes=yes,
        confirm_destructive=confirm_destructive,
        show=not as_json,
    )
    if as_json:
        output.print_json(result)
        return
    if result["status"] == "approved":
        output.success(
            f"{action_id} approved; the action executor applies it as {(result.get('runs_as') or {}).get('identity')}"
        )
    else:
        output.success(
            f"{action_id}: approval {len(result['approvals'])}/{result['required_approvals']} recorded; "
            "another approver must give the next one"
        )


@command(review_app, "approve")
def review_approve(
    action_id: str,
    comment: CommentOpt = None,
    exclude: ExcludeOpt = None,
    yes: YesOpt = False,
    confirm_destructive: ConfirmOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Approve a proposed write after seeing its preview and runs-as identity."""
    _approve(action_id, comment, exclude, yes, confirm_destructive, as_json)


@command(top_level, "approve")
def approve_alias(
    action_id: str,
    comment: CommentOpt = None,
    exclude: ExcludeOpt = None,
    yes: YesOpt = False,
    confirm_destructive: ConfirmOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Approve a proposed write (alias of `z review approve`)."""
    _approve(action_id, comment, exclude, yes, confirm_destructive, as_json)


ReasonArg = Annotated[str, typer.Option("--reason", "-r", help="Why: the agent reads it and learns from it.")]


def _reject(action_id: str, reason: str, as_json: bool) -> None:
    result = review.reject(state.get().api(), action_id, reason)
    if as_json:
        output.print_json(result)
    else:
        output.success(f"{action_id} rejected")


@command(review_app, "reject")
def review_reject(action_id: str, reason: ReasonArg, as_json: JsonOpt = False) -> None:
    """Reject a proposed write and tell the agent why."""
    _reject(action_id, reason, as_json)


@command(top_level, "reject")
def reject_alias(action_id: str, reason: ReasonArg, as_json: JsonOpt = False) -> None:
    """Reject a proposed write (alias of `z review reject`)."""
    _reject(action_id, reason, as_json)


@command(review_app, "edit")
def review_edit(
    action_id: str,
    payload_file: Annotated[
        Path | None, typer.Option("--payload-file", help="JSON file with the new payload ('-' = stdin).")
    ] = None,
    set_: Annotated[
        list[str] | None, typer.Option("--set", help="path=value or path:=<json> on the current payload (repeat).")
    ] = None,
    comment: CommentOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Edit a proposed write; the edit supersedes it and approvals start over. Opens your editor by default."""
    api = state.get().api()
    action = review.get_action(api, action_id)
    review.check_pending(action)
    payload: dict[str, Any] = action.get("payload") or {}
    if payload_file is not None:
        raw = sys.stdin.read() if str(payload_file) == "-" else payload_file.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise UsageError(f"--payload-file: not valid JSON ({exc})") from exc
    if set_:
        payload = review.apply_sets(payload, set_)
    if payload_file is None and not set_:
        payload = review.edit_in_editor(payload)
    if payload == action.get("payload"):
        raise UsageError("nothing changed")
    new = review.edit(api, action_id, payload, comment)
    if as_json:
        output.print_json(new)
        return
    output.err().print(action_panel(new))
    output.success(f"{action_id} superseded by {new['id']}; approve the new one with `z approve {new['id']}`")


@command(top_level, "answer")
def answer(
    question_id: str,
    text: Annotated[str, typer.Argument(help="Your answer (one of the options when the agent gave some).")],
    as_json: JsonOpt = False,
) -> None:
    """Answer an agent's question."""
    result = review.answer(state.get().api(), question_id, text)
    if as_json:
        output.print_json(result)
    else:
        output.success(f"Answered {question_id}; @{result['agent']} continues")
