"""Rich renderings shared by the subcommands, `z top` and the session (design/mockups/CLI.html)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from z_cli.glyphs import MINUS, POINTER
from z_cli.live import JobView, MissionView, StepView, parse_ts, short_args
from z_cli.theme import state_style

MARKS = {
    "succeeded": ("✓", "z.ok"),
    "running": ("◐", "z.run"),
    "pending": ("○", "z.d"),
    "queued": ("○", "z.d"),
    "waiting": ("◐", "z.wait"),
    "needs_attention": ("◐", "z.wait"),
    "paused": ("‖", "z.wait"),
    "failed": ("✕", "z.err"),
    "timed_out": ("✕", "z.err"),
    "budget_exceeded": ("✕", "z.err"),
    "cancelled": (MINUS, "z.d"),
    "skipped": ("↷", "z.d"),
}
JUDGE_TEXT = {"clean": ("no injection", "z.ok"), "flagged": ("flagged", "z.wait"), "blocked": ("blocked", "z.err")}


def mark(status: str) -> tuple[str, str]:
    return MARKS.get(status, ("•", state_style(status)))


# ---------------------------------------------------------------- formatting


def tokens(n: int | None) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = max(0, int(seconds))
    if s < 3600:
        return f"{s // 60:02d}:{s % 60:02d}"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


def age(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def ms(value: int | None) -> str:
    if value is None:
        return ""
    return f"{value}ms" if value < 1000 else f"{value / 1000:.1f}s"


def when(value: Any) -> str:
    ts = value if isinstance(value, datetime) else parse_ts(value)
    if ts is None:
        return ""
    return ts.astimezone().strftime("%Y-%m-%d %H:%M")


def clock(ts: datetime | None) -> str:
    return ts.astimezone().strftime("%H:%M:%S") if ts else "--:--:--"


def since(ts: datetime | None, until: datetime | None = None) -> float | None:
    if ts is None:
        return None
    return ((until or datetime.now(UTC)) - ts).total_seconds()


def status_text(status: str | None, reason: str | None = None) -> Text:
    label = status or "?"
    if status == "waiting" and reason:
        label = reason
    return Text(label, style=state_style(reason if status == "waiting" and reason else status))


# ---------------------------------------------------------------- mission tree


def _step_line(view: MissionView, step: StepView, first: bool) -> Text:
    status = view.step_status(step)
    glyph, style = mark(status)
    line = Text("  ⎿ " if first else "    ", style="z.d")
    line.append(glyph + " ", style=style)
    line.append(step.title, style="z.d" if status in {"pending", "skipped", "cancelled"} else "z.text")
    jobs = view.jobs_for_step(step.id)
    details: list[tuple[str, str]] = []
    if step.fanout or len(jobs) > 1:
        done = sum(1 for j in jobs if j.status == "succeeded")
        details.append((f"{done} / {len(jobs)}", "z.run" if status == "running" else "z.d"))
    elif jobs:
        job = jobs[-1]
        details.append((f"@{job.agent}", "z.d"))
    if step.writes:
        details.append(("writes need approval", "z.d"))
    if details:
        line.append("   ")
        for i, (text, s) in enumerate(details):
            if i:
                line.append(" · ", style="z.d")
            line.append(text, style=s)
    return line


def _job_block(job: JobView, view: MissionView, *, expand: bool, max_calls: int) -> list[Text]:
    glyph_style = state_style(job.status if job.status != "needs_attention" else "waiting")
    head = Text()
    head.append("● ", style=glyph_style)
    head.append(job.agent, style="z.b")
    bits = [job.status if job.status != "needs_attention" else f"waiting: {job.reason or 'attention'}"]
    if job.parent_job_id:
        bits.insert(0, "sub-agent")
    if job.item:
        bits.append(job.item)
    if job.model:
        bits.append(job.model)
    if job.tokens:
        bits.append(f"{tokens(job.tokens)} tokens")
    head.append(" · " + " · ".join(bits), style="z.d")
    lines = [head]
    entries: list[tuple[datetime | None, Text]] = []
    for call in job.calls.values():
        t = Text("  ⎿ ", style="z.d")
        t.append(call.operation or call.tool)
        t.append(short_args(call.args), style="z.d")
        if call.ok is False:
            t.append("  failed", style="z.err")
            if call.summary:
                t.append(f" · {call.summary[:80]}", style="z.d")
        elif call.summary:
            t.append(f"  → {call.summary[:60]}", style="z.d")
        if call.judge and call.judge != "clean":
            label, style = JUDGE_TEXT.get(call.judge, (call.judge, "z.wait"))
            t.append(f"  judge {label}", style=style)
        if call.duration_ms is not None:
            t.append(f"  {ms(call.duration_ms)}", style="z.d")
        elif call.ok is None:
            t.append("  …", style="z.run")
        entries.append((call.ts, t))
    for line in job.lines:
        t = Text("  ⎿ ", style="z.d")
        if line.kind == "blocked":
            t.append("blocked ", style="z.wait")
            t.append(line.text)
            if line.detail:
                t.append(f" · {line.detail}", style="z.d")
        elif line.kind == "terminal":
            t.append("$ ", style="z.d")
            t.append(line.text)
            if line.detail:
                t.append(f"  {line.detail}", style="z.err" if line.detail != "exit 0" else "z.d")
        elif line.kind == "message":
            if line.detail == "user":
                t.append("you: ", style="z.acc")
            t.append(line.text[:300] if not expand else line.text)
        elif line.kind == "thought":
            if not expand:
                continue
            t.append(line.text, style="z.d")
        elif line.kind in {"question", "proposed", "invalid", "judge"}:
            t.append(f"{line.kind} ", style="z.wait")
            t.append(line.text)
        else:
            t.append(f"{line.kind} ", style="z.d")
            t.append(line.text)
        entries.append((line.ts, t))
    entries.sort(key=lambda e: e[0] or datetime.min.replace(tzinfo=UTC))
    if not expand and len(entries) > max_calls:
        hidden = len(entries) - max_calls
        lines.append(Text(f"  ⎿ … {hidden} earlier", style="z.d"))
        entries = entries[-max_calls:]
    lines += [t for _, t in entries]
    if expand and job.last_output:
        lines.append(Text("  output:", style="z.d"))
        lines += [Text("    " + row, style="z.m") for row in job.last_output.splitlines()[-40:]]
    return lines


def mission_tree(view: MissionView, *, expand: bool = False, max_calls: int = 6, show_jobs: bool = True) -> Group:
    rows: list[RenderableType] = []
    head = Text()
    glyph_style = state_style(view.status if view.status != "waiting" else "waiting")
    head.append("● ", style=glyph_style)
    if view.playbook:
        head.append("Playbook ", style="z.text")
        head.append(view.playbook, style="z.b")
        if view.playbook_version:
            head.append(f" v{view.playbook_version}", style="z.d")
    else:
        head.append(view.title or view.mission_id, style="z.b")
    bits = [view.status if view.status != "waiting" else f"waiting for {view.waiting_reason or 'you'}"]
    if view.writes:
        bits.append("writes need your approval")
    if view.autonomy == "read_only":
        bits.append("read only")
    bits.append(view.mission_id)
    head.append(" · " + " · ".join(bits), style="z.d")
    rows.append(head)
    for i, step in enumerate(view.steps.values()):
        rows.append(_step_line(view, step, i == 0))
    if show_jobs:
        for job in view.jobs.values():
            if job.status == "queued" and not job.calls and not job.lines:
                continue
            rows.append(Text(""))
            rows.extend(_job_block(job, view, expand=expand, max_calls=max_calls))
    if view.terminal:
        rows.append(Text(""))
        end = Text()
        glyph, style = mark(view.status)
        end.append(f"{glyph} ", style=style)
        end.append(f"mission {view.status}", style=style)
        end.append(f" · {tokens(view.tokens)} tokens", style="z.d")
        if view.error and view.status != "succeeded":
            end.append(f" · {view.error}", style="z.d")
        rows.append(end)
    return Group(*rows)


# ---------------------------------------------------------------- one-line events (logs, watch, z top live pane)


def event_line(event: dict[str, Any], *, with_agent: bool = True) -> Text:
    p: dict[str, Any] = event.get("payload") or {}
    etype = str(event.get("type"))
    line = Text(clock(parse_ts(event.get("ts"))) + " ", style="z.d")
    if with_agent:
        line.append(f"{(event.get('agent') or 'mission')[:16]:<16} ", style="z.m")
    label, style, text, extra = etype, "z.text", "", ""
    if etype == "status":
        label, text = "status", f"{p.get('entity')} {p.get('from') or '·'} → {p.get('to')}"
        style = state_style(str(p.get("to")))
        extra = str(p.get("reason") or "")
    elif etype == "plan":
        text = f"{len(p.get('steps') or [])} steps" + (f" · writes {', '.join(p['writes'])}" if p.get("writes") else "")
    elif etype == "message":
        label, text = str(p.get("role", "message")), str(p.get("text", "")).replace("\n", " ")[:200]
    elif etype == "thought":
        label, style, text = "thought", "z.d", str(p.get("text", "")).replace("\n", " ")[:160]
    elif etype == "tool_call":
        label, text = "tool", f"{p.get('operation') or p.get('tool')}{short_args(dict(p.get('args') or {}))}"
    elif etype == "tool_result":
        ok = bool(p.get("ok"))
        label, style = ("result", "z.ok") if ok else ("error", "z.err")
        text = str(p.get("summary", ""))[:160]
        extra = f"{ms(p.get('duration_ms'))} · judge {p.get('judge')}"
    elif etype == "terminal":
        code = p.get("exit_code")
        label, text = "terminal", str(p.get("command", ""))
        extra = f"exit {code}" if code is not None else ""
        if code not in (None, 0):
            style = "z.err"
    elif etype == "file_edit":
        label, text = "edit", f"{p.get('path')}  +{p.get('added', 0)} {MINUS}{p.get('removed', 0)}"
    elif etype == "browser":
        label, text = "browser", f"{p.get('action')} {p.get('url')}"
    elif etype == "denied":
        label, style, text = "blocked", "z.wait", str(p.get("what", ""))
        extra = str(p.get("detail") or p.get("rule") or "")
    elif etype == "validation_error":
        label, style = "invalid", "z.wait"
        text = f"{p.get('where')}: " + "; ".join(
            f"{e.get('path')}: {e.get('message')}" for e in p.get("errors", [])[:3]
        )
    elif etype == "usage":
        label, style = "usage", "z.d"
        text = f"{p.get('model')} in {p.get('tokens_in')} out {p.get('tokens_out')}"
        extra = ms(p.get("latency_ms"))
    elif etype == "artifact":
        label, text = "artifact", f"{p.get('kind')} {p.get('name')}"
        extra = str(p.get("artifact_id", ""))
    elif etype == "question":
        label, style = "question", "z.wait"
        text = f'"{p.get("text")}"'
        extra = " / ".join(p.get("options") or [])
    elif etype == "question_answered":
        label, text = "answered", f"{p.get('answer')}"
        extra = f"by {p.get('answered_by')}"
    elif etype == "action_proposed":
        label, style = "approval", "z.wait"
        text = f"{p.get('operation')} · {p.get('summary')}"
        extra = f"{p.get('class')} · {p.get('action_id')}"
    elif etype == "action_status":
        label, text = "action", f"{p.get('action_id')} {p.get('from')} → {p.get('to')}"
        style = state_style(str(p.get("to")))
        extra = f"by {p['by']}" if p.get("by") else ""
    elif etype == "subagent":
        label, text = "sub-agent", f"{p.get('agent')} · {p.get('input_summary', '')}"
    elif etype == "checkpoint":
        label, style, text = "checkpoint", "z.d", f"step {p.get('step')} · {p.get('size')} bytes"
    elif etype == "data_class":
        label, style, text = "data class", "z.wait", f"{p.get('from')} → {p.get('to')} ({p.get('cause')})"
    elif etype == "judge":
        verdict = str(p.get("verdict"))
        label, style = "judge", {"clean": "z.ok", "flagged": "z.wait", "blocked": "z.err"}.get(verdict, "z.text")
        text = f"{verdict} {p.get('subject')}"
        extra = str(p.get("reason") or "")
    else:
        text = json.dumps(p, default=str)[:160]
    line.append(f"{label:<10} ", style=style)
    line.append(text, style="z.m" if style == "z.text" else style)
    if extra:
        line.append(f"  {extra}", style="z.d")
    return line


# ---------------------------------------------------------------- actions and questions


def _judge(action: dict[str, Any]) -> str:
    verdict = action.get("judge_verdict") or {}
    if not verdict:
        return "judge: not run"
    v = verdict.get("verdict")
    if v == "ok":
        return "judge: no policy issue"
    failed = [c.get("name") for c in verdict.get("checks", []) if not c.get("ok")]
    return f"judge: {v}" + (f" ({', '.join(str(f) for f in failed)})" if failed else "")


def action_header(action: dict[str, Any]) -> Group:
    destructive = action.get("class") == "destructive"
    title = Text()
    title.append(
        "Approve destructive action" if destructive else "Approve write", style="z.err" if destructive else "z.wait"
    )
    title.append(f"  {action.get('connector')} · {action.get('operation')}", style="z.d")
    title.append(f" · {action.get('summary')}", style="z.text")
    runs = action.get("runs_as") or {}
    sub = Text("runs as ", style="z.d")
    sub.append(str(runs.get("identity", "?")), style="z.b")
    sub.append(f" ({runs.get('scope', '?')} credential)", style="z.d")
    sub.append(
        " · reversible" if action.get("reversible") else " · NOT reversible",
        style="z.d" if action.get("reversible") else "z.err",
    )
    sub.append(f" · {_judge(action)}", style="z.d")
    approvals = action.get("approvals") or []
    need = int(action.get("required_approvals") or 1)
    status = Text(f"status {action.get('status')} · approvals {len(approvals)}/{need}", style="z.d")
    if approvals:
        status.append(" · by " + ", ".join(str(a.get("by")) for a in approvals), style="z.d")
    if action.get("four_eyes"):
        status.append(" · four-eyes: the mission starter cannot approve", style="z.wait")
    status.append(f" · {action.get('id')}", style="z.d")
    return Group(title, sub, status)


def _rows(pairs: list[tuple[str, Any]]) -> list[Text]:
    out = []
    for key, value in pairs:
        if value in (None, "", [], {}):
            continue
        t = Text(f"  {key:<12} ", style="z.d")
        t.append(", ".join(map(str, value)) if isinstance(value, list) else str(value))
        out.append(t)
    return out


def preview_body(preview: dict[str, Any], payload: dict[str, Any] | None = None) -> list[RenderableType]:
    kind = preview.get("kind")
    rows: list[RenderableType] = []
    if kind == "issue":
        rows += _rows(
            [
                ("tracker", f"{preview.get('tracker')} · project {preview.get('project')}"),
                ("title", preview.get("title")),
                ("type", preview.get("issue_type")),
                ("priority", preview.get("priority")),
                ("labels", preview.get("labels")),
                ("assignee", preview.get("assignee")),
                ("due", preview.get("due_date")),
            ]
        )
        if preview.get("description"):
            rows.append(Text(""))
            rows += [Text("  " + line) for line in str(preview["description"]).splitlines()[:30]]
    elif kind == "issue_update":
        rows.append(Text(f"  {preview.get('tracker')} {preview.get('issue')}", style="z.b"))
        for change in preview.get("changes") or []:
            t = Text(f"  {change.get('field')}: ", style="z.d")
            t.append(json.dumps(change.get("from"), default=str), style="z.err")
            t.append(" → ", style="z.d")
            t.append(json.dumps(change.get("to"), default=str), style="z.ok")
            rows.append(t)
    elif kind == "comment":
        rows += _rows([("on", f"{preview.get('tracker') or ''} {preview.get('target')}".strip())])
        rows += [Text("  " + line) for line in str(preview.get("body", "")).splitlines()[:40]]
    elif kind == "mail":
        rows += _rows(
            [
                ("from", preview.get("from")),
                ("to", preview.get("to")),
                ("cc", preview.get("cc")),
                ("subject", preview.get("subject")),
                ("in reply to", preview.get("in_reply_to")),
                ("attachments", preview.get("attachments")),
            ]
        )
        rows.append(Text(""))
        rows += [Text("  " + line) for line in str(preview.get("body", "")).splitlines()[:40]]
    elif kind == "event":
        rows += _rows(
            [
                ("title", preview.get("title")),
                ("start", preview.get("start")),
                ("end", preview.get("end")),
                ("attendees", preview.get("attendees")),
                ("location", preview.get("location")),
            ]
        )
        if preview.get("body"):
            rows += [Text("  " + line) for line in str(preview["body"]).splitlines()[:20]]
    elif kind == "chat_message":
        rows += _rows([("to", f"{preview.get('platform')} {preview.get('target')}")])
        rows += [Text("  " + line) for line in str(preview.get("text", "")).splitlines()[:30]]
    elif kind == "file":
        rows += _rows(
            [
                ("file", preview.get("name")),
                ("size", preview.get("size")),
                ("destination", preview.get("destination")),
                ("artifact", preview.get("artifact_id")),
            ]
        )
    elif kind == "diff":
        rows += _rows(
            [
                ("project", preview.get("project")),
                ("branches", f"{preview.get('source_branch')} → {preview.get('target_branch')}"),
                ("title", preview.get("title")),
                ("patch", preview.get("patch_artifact")),
            ]
        )
        for f in preview.get("files") or []:
            t = Text(f"  {f.get('path')}  ", style="z.text")
            t.append(f"+{f.get('added', 0)}", style="z.ok")
            t.append(f" {MINUS}{f.get('removed', 0)}", style="z.err")
            rows.append(t)
        if preview.get("description"):
            rows += [Text("  " + line, style="z.m") for line in str(preview["description"]).splitlines()[:10]]
        if preview.get("diff"):
            rows.append(Syntax(str(preview["diff"])[:20000], "diff", theme="ansi_dark", background_color="default"))
    else:
        data = preview.get("data", payload) if kind == "json" else preview
        rows.append(
            Syntax(
                json.dumps(data, indent=2, default=str)[:20000], "json", theme="ansi_dark", background_color="default"
            )
        )
    items = (payload or {}).get("items")
    if isinstance(items, list) and items and kind != "json":
        rows.append(Text(""))
        for i, item in enumerate(items, 1):
            label = item.get("title") or item.get("summary") or item.get("id") if isinstance(item, dict) else item
            t = Text(f"  {i}  ", style="z.d")
            t.append(str(label))
            if isinstance(item, dict) and item.get("id") is not None:
                t.append(f"  id {item.get('id')}", style="z.d")
            rows.append(t)
    return rows


def action_panel(action: dict[str, Any], *, options: list[str] | None = None, selected: int = 0) -> Panel:
    body: list[RenderableType] = [action_header(action), Text("")]
    body += preview_body(action.get("preview") or {}, action.get("payload"))
    if options:
        body.append(Text(""))
        for i, option in enumerate(options):
            t = Text()
            t.append(f"{POINTER} " if i == selected else "  ", style="z.acc")
            t.append(f"{i + 1}. {option}", style="z.b" if i == selected else "z.text")
            body.append(t)
    destructive = action.get("class") == "destructive"
    return Panel(Group(*body), border_style="z.err" if destructive else "z.wait", padding=(0, 2), expand=False)


def question_panel(question: dict[str, Any], *, selected: int = 0, show_options: bool = True) -> Panel:
    body: list[RenderableType] = []
    head = Text()
    head.append("Question", style="z.wait")
    head.append(f"  @{question.get('agent')} · {question.get('id')}", style="z.d")
    body += [head, Text(""), Text(str(question.get("text", "")))]
    if question.get("answer"):
        body.append(Text(f"answered by {question.get('answered_by')}: {question.get('answer')}", style="z.ok"))
    elif show_options and question.get("options"):
        body.append(Text(""))
        for i, option in enumerate(question["options"]):
            t = Text()
            t.append(f"{POINTER} " if i == selected else "  ", style="z.acc")
            t.append(f"{i + 1}. {option}", style="z.b" if i == selected else "z.text")
            body.append(t)
    return Panel(Group(*body), border_style="z.wait", padding=(0, 2), expand=False)
