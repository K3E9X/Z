"""Fold a mission's event stream (contracts/events.schema.json) into a live view: plan steps, one entry per
agent job with its tool calls, judge verdicts and blocked calls, pending decisions, tokens.

Pure and synchronous: `z missions watch`, `z top` and the interactive session all render from it.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from z_cli.glyphs import MINUS

TERMINAL_MISSION = {"succeeded", "failed", "cancelled", "budget_exceeded", "timed_out"}
TERMINAL_JOB = {"succeeded", "failed", "cancelled", "budget_exceeded", "timed_out"}
RECENT = 200


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class ToolCall:
    call_id: str
    tool: str
    operation: str | None
    args: dict[str, Any]
    ts: datetime | None = None
    ok: bool | None = None
    summary: str = ""
    duration_ms: int | None = None
    judge: str | None = None


@dataclass
class Line:
    """Something an agent did that is not a tool call (blocked call, terminal, edit, message, question...)."""

    kind: str
    text: str
    ts: datetime | None = None
    detail: str = ""


@dataclass
class JobView:
    id: str
    agent: str
    step_id: str | None = None
    status: str = "queued"
    reason: str | None = None
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    started: datetime | None = None
    ended: datetime | None = None
    item: str | None = None
    parent_job_id: str | None = None
    calls: OrderedDict[str, ToolCall] = field(default_factory=OrderedDict)
    lines: list[Line] = field(default_factory=list)
    last_output: str = ""

    @property
    def active(self) -> bool:
        return self.status not in TERMINAL_JOB

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out


@dataclass
class StepView:
    id: str
    title: str
    agent: str
    status: str = "pending"
    writes: list[str] = field(default_factory=list)
    fanout: bool = False


@dataclass
class ActionView:
    id: str
    summary: str
    connector: str
    operation: str
    class_: str
    status: str = "pending"
    job_id: str | None = None
    agent: str | None = None
    by: str | None = None


@dataclass
class QuestionView:
    id: str
    text: str
    options: list[str]
    job_id: str | None = None
    agent: str | None = None
    answer: str | None = None
    answered_by: str | None = None


@dataclass
class LogEntry:
    ts: datetime | None
    kind: str
    text: str
    agent: str | None
    job_id: str | None
    style: str = ""


@dataclass
class MissionView:
    mission_id: str
    title: str = ""
    status: str = "draft"
    waiting_reason: str | None = None
    playbook: str | None = None
    playbook_version: str | None = None
    autonomy: str | None = None
    data_class: str | None = None
    plan_first: bool = False
    created_at: datetime | None = None
    error: str | None = None
    steps: OrderedDict[str, StepView] = field(default_factory=OrderedDict)
    jobs: OrderedDict[str, JobView] = field(default_factory=OrderedDict)
    actions: OrderedDict[str, ActionView] = field(default_factory=OrderedDict)
    questions: OrderedDict[str, QuestionView] = field(default_factory=OrderedDict)
    tokens_in: int = 0
    tokens_out: int = 0
    last_seq: int = 0
    log: deque[LogEntry] = field(default_factory=lambda: deque(maxlen=RECENT))
    messages: list[tuple[str, str]] = field(default_factory=list)  # (role/by, text)
    writes: list[str] = field(default_factory=list)

    # ---------------------------------------------------------------- derived

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_MISSION

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def pending_actions(self) -> list[ActionView]:
        return [a for a in self.actions.values() if a.status == "pending"]

    def open_questions(self) -> list[QuestionView]:
        return [q for q in self.questions.values() if q.answer is None]

    def jobs_for_step(self, step_id: str) -> list[JobView]:
        return [j for j in self.jobs.values() if j.step_id == step_id]

    def step_status(self, step: StepView) -> str:
        jobs = self.jobs_for_step(step.id)
        if step.status in {"skipped", "failed", "cancelled"} or not jobs:
            return step.status
        states = {j.status for j in jobs}
        if "running" in states:
            return "running"
        if "needs_attention" in states:
            return "waiting"
        if "paused" in states:
            return "paused"
        if "queued" in states:
            return "running" if states & {"succeeded"} else "pending"
        if states <= {"succeeded"}:
            return "succeeded"
        if states & {"failed", "timed_out", "budget_exceeded"}:
            return "failed"
        return step.status

    # ---------------------------------------------------------------- seeding from the REST API

    def load_mission(self, mission: dict[str, Any]) -> None:
        self.title = str(mission.get("title") or self.title)
        self.status = str(mission.get("status") or self.status)
        self.waiting_reason = mission.get("waiting_reason")
        self.playbook = mission.get("playbook")
        self.playbook_version = mission.get("playbook_version")
        self.autonomy = mission.get("autonomy")
        self.data_class = mission.get("data_class")
        self.plan_first = bool(mission.get("plan_first"))
        self.created_at = parse_ts(mission.get("created_at"))
        self.error = mission.get("error")
        self.tokens_in = max(self.tokens_in, int(mission.get("tokens_in") or 0))
        self.tokens_out = max(self.tokens_out, int(mission.get("tokens_out") or 0))
        plan = mission.get("plan") or {}
        if plan.get("steps"):
            self._load_plan(plan.get("steps") or [], plan.get("writes"))

    def load_job(self, job: dict[str, Any]) -> JobView:
        view = self._job(str(job["id"]), str(job.get("agent") or "?"), job.get("step_id"))
        view.status = str(job.get("status") or view.status)
        view.reason = job.get("needs_attention_reason")
        view.model = job.get("model") or view.model
        view.tokens_in = max(view.tokens_in, int(job.get("tokens_in") or 0))
        view.tokens_out = max(view.tokens_out, int(job.get("tokens_out") or 0))
        view.started = parse_ts(job.get("started_at")) or view.started
        view.ended = parse_ts(job.get("ended_at")) or view.ended
        view.item = job.get("item_key")
        view.parent_job_id = job.get("parent_job_id")
        return view

    def load_review_item(self, item: dict[str, Any]) -> None:
        if item.get("kind") == "action" and item.get("action"):
            a = item["action"]
            view = self.actions.get(a["id"]) or ActionView(
                a["id"], a.get("summary", ""), a.get("connector", ""), a.get("operation", ""), a.get("class", "write")
            )
            view.status, view.job_id, view.agent = a.get("status", view.status), a.get("job_id"), a.get("agent")
            self.actions[a["id"]] = view
        elif item.get("kind") == "question" and item.get("question"):
            q = item["question"]
            qv = self.questions.get(q["id"]) or QuestionView(q["id"], q.get("text", ""), list(q.get("options") or []))
            qv.job_id, qv.agent, qv.answer, qv.answered_by = (
                q.get("job_id"),
                q.get("agent"),
                q.get("answer"),
                q.get("answered_by"),
            )
            self.questions[q["id"]] = qv

    # ---------------------------------------------------------------- events

    def _load_plan(self, steps: list[dict[str, Any]], writes: list[str] | None) -> None:
        for raw in steps:
            sid = str(raw.get("id"))
            step = self.steps.get(sid) or StepView(sid, str(raw.get("title") or raw.get("prompt") or sid), "")
            step.title = str(raw.get("title") or (raw.get("prompt") or sid).split("\n")[0])[:120]
            step.agent = str(raw.get("agent") or step.agent)
            step.writes = list(raw.get("writes") or [])
            step.fanout = bool(raw.get("fanout"))
            if raw.get("status"):
                step.status = str(raw["status"])
            self.steps[sid] = step
        if writes is not None:
            self.writes = list(writes)

    def _job(self, job_id: str, agent: str | None, step_id: str | None) -> JobView:
        view = self.jobs.get(job_id)
        if view is None:
            view = JobView(job_id, agent or "?", step_id)
            self.jobs[job_id] = view
        if agent and view.agent == "?":
            view.agent = agent
        if step_id and not view.step_id:
            view.step_id = step_id
        return view

    def _log(
        self, ts: datetime | None, kind: str, text: str, agent: str | None, job: str | None, style: str = ""
    ) -> None:
        self.log.append(LogEntry(ts, kind, text, agent, job, style))

    def apply(self, event: dict[str, Any]) -> None:
        seq = int(event.get("seq") or 0)
        if seq and seq <= self.last_seq:
            return  # replayed after a reconnect
        self.last_seq = max(self.last_seq, seq)
        etype = str(event.get("type"))
        p: dict[str, Any] = event.get("payload") or {}
        ts = parse_ts(event.get("ts"))
        job_id = event.get("job_id")
        agent = event.get("agent")
        job = self._job(job_id, agent, event.get("step_id")) if job_id else None

        if etype == "status":
            entity, to = p.get("entity"), str(p.get("to"))
            if entity == "mission":
                self.status = to
                if to == "waiting":
                    self.waiting_reason = (p.get("reason") or "").split(" ")[0] or self.waiting_reason
                if p.get("reason") and to in TERMINAL_MISSION and to != "succeeded":
                    self.error = str(p.get("reason"))
                self._log(ts, "status", f"mission {to}" + (f" · {p['reason']}" if p.get("reason") else ""), None, None)
            elif entity == "job":
                target = self._job(str(p.get("id") or job_id), agent, event.get("step_id"))
                target.status = to
                if to == "running" and target.started is None:
                    target.started = ts
                if to in TERMINAL_JOB:
                    target.ended = ts
                if to == "needs_attention":
                    target.reason = p.get("reason")
                self._log(ts, "status", f"job {to}" + (f" · {p['reason']}" if p.get("reason") else ""), agent, job_id)
            elif entity == "step":
                sid = str(p.get("id") or event.get("step_id"))
                if sid in self.steps:
                    self.steps[sid].status = to
        elif etype == "plan":
            self._load_plan(list(p.get("steps") or []), p.get("writes"))
        elif etype == "message":
            role = str(p.get("role"))
            text = str(p.get("text", ""))
            self.messages.append((p.get("by") or role, text))
            if job is not None:
                job.lines.append(Line("message", text, ts, role))
            self._log(ts, "message", text, agent, job_id)
        elif etype == "thought":
            if job is not None:
                job.lines.append(Line("thought", str(p.get("text", "")), ts))
        elif etype == "tool_call" and job is not None:
            call = ToolCall(
                str(p.get("call_id")), str(p.get("tool")), p.get("operation"), dict(p.get("args") or {}), ts
            )
            job.calls[call.call_id] = call
            self._log(ts, "tool", f"{call.operation or call.tool}{short_args(call.args)}", agent, job_id)
        elif etype == "tool_result" and job is not None:
            call = job.calls.get(str(p.get("call_id"))) or ToolCall(str(p.get("call_id")), "?", None, {}, ts)
            call.ok, call.summary = bool(p.get("ok")), str(p.get("summary", ""))
            call.duration_ms, call.judge = p.get("duration_ms"), p.get("judge")
            job.calls[call.call_id] = call
            job.last_output = call.summary
            if call.judge in {"flagged", "blocked"}:
                self._log(ts, "judge", f"{call.judge}: {call.operation or call.tool}", agent, job_id, "z.wait")
        elif etype == "terminal" and job is not None:
            text = str(p.get("command", ""))
            detail = str(p.get("output_tail") or "")
            code = p.get("exit_code")
            job.lines.append(Line("terminal", text, ts, f"exit {code}" if code is not None else ""))
            if detail:
                job.last_output = detail
            self._log(
                ts,
                "terminal",
                text + (f"  exit {code}" if code is not None else ""),
                agent,
                job_id,
                "z.err" if code not in (None, 0) else "",
            )
        elif etype == "file_edit" and job is not None:
            text = f"{p.get('path')}  +{p.get('added', 0)} {MINUS}{p.get('removed', 0)}"
            job.lines.append(Line("edit", text, ts))
            self._log(ts, "edit", text, agent, job_id)
        elif etype == "browser" and job is not None:
            text = f"{p.get('action')} {p.get('url')}"
            job.lines.append(Line("browser", text, ts))
            self._log(ts, "browser", text, agent, job_id)
        elif etype == "denied":
            text = f"{p.get('what')} · {p.get('detail') or p.get('rule')}"
            if job is not None:
                job.lines.append(
                    Line("blocked", str(p.get("what", "")), ts, str(p.get("detail") or p.get("rule") or ""))
                )
            self._log(ts, "blocked", text, agent, job_id, "z.wait")
        elif etype == "validation_error":
            errors = p.get("errors") or []
            text = f"invalid {p.get('where')}: " + "; ".join(f"{e.get('path')}: {e.get('message')}" for e in errors[:3])
            if job is not None:
                job.lines.append(Line("invalid", text, ts))
            self._log(ts, "invalid", text, agent, job_id, "z.wait")
        elif etype == "usage":
            tin, tout = int(p.get("tokens_in") or 0), int(p.get("tokens_out") or 0)
            self.tokens_in += tin
            self.tokens_out += tout
            if job is not None:
                job.tokens_in += tin
                job.tokens_out += tout
                job.model = str(p.get("model") or job.model or "")
        elif etype == "artifact":
            text = f"{p.get('kind')} {p.get('name')}"
            if job is not None:
                job.lines.append(Line("artifact", text, ts, str(p.get("artifact_id", ""))))
            self._log(ts, "artifact", text, agent, job_id)
        elif etype == "question":
            q = QuestionView(
                str(p.get("question_id")), str(p.get("text", "")), list(p.get("options") or []), job_id, agent
            )
            self.questions.setdefault(q.id, q)
            if job is not None:
                job.lines.append(Line("question", q.text, ts, q.id))
            self._log(ts, "question", f'"{q.text}"', agent, job_id, "z.wait")
        elif etype == "question_answered":
            qid = str(p.get("question_id"))
            qv = self.questions.get(qid)
            if qv is not None:
                qv.answer, qv.answered_by = str(p.get("answer")), p.get("answered_by")
            self._log(ts, "answer", f"{p.get('answered_by')}: {p.get('answer')}", agent, job_id)
        elif etype == "action_proposed":
            a = ActionView(
                str(p.get("action_id")),
                str(p.get("summary", "")),
                str(p.get("connector", "")),
                str(p.get("operation", "")),
                str(p.get("class", "write")),
                job_id=job_id,
                agent=agent,
            )
            self.actions.setdefault(a.id, a)
            if job is not None:
                job.lines.append(Line("proposed", f"{a.operation} · {a.summary}", ts, a.id))
            self._log(ts, "approval", f"{a.operation} · {a.summary}", agent, job_id, "z.wait")
        elif etype == "action_status":
            aid = str(p.get("action_id"))
            av = self.actions.get(aid)
            if av is not None:
                av.status, av.by = str(p.get("to")), p.get("by")
            self._log(
                ts,
                "action",
                f"{aid} {p.get('from')} → {p.get('to')}" + (f" by {p['by']}" if p.get("by") else ""),
                agent,
                job_id,
            )
        elif etype == "subagent":
            child = self._job(str(p.get("child_job_id")), str(p.get("agent")), None)
            child.parent_job_id = job_id
            if job is not None:
                job.lines.append(Line("subagent", f"{p.get('agent')} · {p.get('input_summary', '')}", ts))
        elif etype == "data_class":
            self.data_class = str(p.get("to"))
            self._log(ts, "data", f"data class {p.get('from')} → {p.get('to')} ({p.get('cause')})", agent, job_id)
        elif etype == "judge":
            verdict = str(p.get("verdict"))
            if verdict != "clean":
                text = f"judge {verdict} {p.get('subject')}" + (f": {p['reason']}" if p.get("reason") else "")
                if job is not None:
                    job.lines.append(Line("judge", text, ts, verdict))
                self._log(ts, "judge", text, agent, job_id, "z.wait" if verdict == "flagged" else "z.err")


def short_args(args: dict[str, Any], width: int = 48) -> str:
    """`(id=18422, project="SEC")` — the shape of the mockup, trimmed to `width`."""
    if not args:
        return "()"
    parts = []
    for key, value in args.items():
        if isinstance(value, str):
            rendered = f'"{value}"' if len(value) <= 40 else f'"{value[:37]}…"'
        elif isinstance(value, int | float | bool) or value is None:
            rendered = str(value)
        else:
            rendered = "…"
        parts.append(f"{key}={rendered}")
    text = ", ".join(parts)
    if len(text) > width:
        text = text[: width - 1] + "…"
    return f"({text})"
