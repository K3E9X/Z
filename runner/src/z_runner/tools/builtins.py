"""Built-in tools that call the internal API (docs/04 §1): propose_action, ask_human, search_memory,
spawn_subagent. `report_objective` is P1 and not offered yet."""

from __future__ import annotations

import json
import time
from typing import Any

from z_common.models.api_internal import ActionIn, MemorySearch, QuestionIn, SubagentIn, WriteTarget
from z_common.models.enums import JobStatus

from z_runner.client import ApiError
from z_runner.tools.base import ArgumentError, ToolContext, ToolResult, ZTool, untrusted, validate_args

STEP_WRITES = "step_writes"
POLL_S = 2.0


def _problem_text(exc: ApiError) -> str:
    if exc.problem is not None:
        return exc.problem.detail or exc.problem.title
    return str(exc)


class ProposeActionTool(ZTool):
    """Every external write is a proposal for a human to approve (invariant 4)."""

    name = "propose_action"
    kind = "propose_action"

    def __init__(self, writes: list[WriteTarget]) -> None:
        self.writes = {w.operation: w for w in writes}
        allowed = "\n".join(
            f"- {w.operation} ({w.class_}){': ' + w.description if w.description else ''}\n  payload schema: "
            + json.dumps(w.input_schema, separators=(",", ":"))
            for w in writes
        )
        self.description = (
            "Propose a change to an external system. A human reviews and approves it; nothing is applied by this "
            "call. Only these operations can be proposed in this step:\n" + allowed
        )
        self.input_schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation", "payload", "summary"],
            "properties": {
                "operation": {"type": "string", "description": "Full operation id, one of: " + ", ".join(self.writes)},
                "payload": {
                    "type": "object",
                    "description": "Arguments of the operation, matching its payload schema.",
                },
                "summary": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                    "description": "One sentence for the reviewer.",
                },
            },
        }

    def operation(self, args: dict[str, Any]) -> str | None:
        op = args.get("operation")
        return op if isinstance(op, str) else None

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        operation = str(args["operation"])
        target = self.writes.get(operation)
        if target is None:
            detail = f"{operation} is not a write of this step (allowed: {', '.join(self.writes) or 'none'})"
            ctx.deny(operation, STEP_WRITES, detail)
            return ToolResult(
                text=f"Refused by the platform guardrail `{STEP_WRITES}`: {detail}.", ok=False, summary="refused"
            )
        errors = validate_args(target.input_schema, args["payload"])
        if errors:
            raise ArgumentError(
                [
                    e.model_copy(update={"path": "payload" if e.path == "(root)" else f"payload/{e.path}"})
                    for e in errors
                ]
            )
        try:
            action = ctx.client.propose_action(
                ActionIn(
                    connector=target.connector,
                    operation=operation,
                    payload=dict(args["payload"]),
                    summary=str(args["summary"]),
                )
            )
        except ApiError as exc:
            if exc.status == 403:
                return ToolResult(
                    text=f"The platform refused this proposal: {_problem_text(exc)}",
                    ok=False,
                    summary="refused by z-server",
                )
            if exc.status == 422:
                return ToolResult(
                    text=f"The proposal is invalid: {_problem_text(exc)}. Fix the payload and propose again.", ok=False
                )
            raise
        ctx.progress.append(f"action:{action.id}")
        return ToolResult(
            text=(
                f"Proposed action {action.id} ({operation}) is {action.status}, waiting for human approval. "
                "Do not propose it again; continue with the rest of the task."
            ),
            summary=f"proposed {operation} ({action.id})",
        )


class AskHumanTool(ZTool):
    name = "ask_human"
    kind = "ask_human"
    description = (
        "Ask a human a question when you cannot continue without a decision or missing information. The job "
        "waits until someone answers. Prefer giving short options."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["question"],
        "properties": {
            "question": {"type": "string", "minLength": 1, "maxLength": 4000},
            "options": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 10},
        },
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        options = list(args.get("options") or [])
        question = ctx.client.ask_question(QuestionIn(text=str(args["question"]), options=options or None))
        payload: dict[str, Any] = {"question_id": question.id, "text": question.text}
        if options:
            payload["options"] = options
        ctx.sink.emit("question", payload)
        while True:
            answer = ctx.control.take_answer(question.id)
            if answer is not None:
                ctx.progress.append(f"answer:{question.id}")
                return ToolResult(
                    text=f"Answer from {answer.answered_by}: {ctx.redactor.text(answer.answer)}",
                    summary=f"answered by {answer.answered_by}",
                )
            if ctx.control.cancelled:
                return ToolResult(
                    text="The job was cancelled while waiting for an answer.", ok=False, summary="cancelled"
                )
            remaining = ctx.remaining_s()
            if remaining <= 1:
                return ToolResult(text="No answer arrived before the job deadline.", ok=False, summary="no answer")
            ctx.control.poll(int(min(30, max(1, remaining - 1))))


class SearchMemoryTool(ZTool):
    name = "search_memory"
    kind = "search_memory"
    read_only = True
    description = "Search approved organisation, team and agent knowledge (memory facts) relevant to a query."
    input_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        snippets = ctx.client.search_memory(MemorySearch(query=str(args["query"]), top_k=args.get("top_k")))
        if not snippets:
            return ToolResult(text="No memory facts match.", summary="0 facts")
        text = "\n".join(f"- [{s.scope}] {ctx.redactor.text(s.text)} (fact {s.id})" for s in snippets)
        return ToolResult(text=text, summary=f"{len(snippets)} fact(s)")


_TERMINAL_CHILD = frozenset(s.value for s in JobStatus if s.terminal) | {JobStatus.needs_attention.value}


class SpawnSubagentTool(ZTool):
    name = "spawn_subagent"
    kind = "spawn_subagent"
    description = (
        "Delegate a sub-task to another agent of the roster and wait for its result. The scheduler enforces this "
        "job's remaining budget; budget_share is the fraction of it the child may use."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["agent", "prompt"],
        "properties": {
            "agent": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,62}$"},
            "prompt": {"type": "string", "minLength": 1},
            "input": {"type": "object"},
            "budget_share": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
        },
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        request = SubagentIn(
            agent=str(args["agent"]),
            prompt=str(args["prompt"]),
            input=args.get("input"),
            budget_share=args.get("budget_share"),
        )
        try:
            status = ctx.client.spawn_subagent(request)
        except ApiError as exc:
            if exc.status in (403, 404, 422, 402):
                return ToolResult(text=f"The sub-agent could not be started: {_problem_text(exc)}", ok=False)
            raise
        ctx.sink.emit(
            "subagent",
            {"child_job_id": status.child_job_id, "agent": request.agent, "input_summary": request.prompt[:300]},
        )
        while status.status.value not in _TERMINAL_CHILD:
            if ctx.control.cancelled:
                return ToolResult(
                    text=f"The job was cancelled while waiting for sub-agent {status.child_job_id}.", ok=False
                )
            if ctx.remaining_s() <= POLL_S:
                return ToolResult(text=f"Sub-agent {status.child_job_id} did not finish before the deadline.", ok=False)
            ctx.control.poll(0)
            time.sleep(POLL_S)
            status = ctx.client.get_subagent(status.child_job_id)
        ctx.progress.append(f"subagent:{status.child_job_id}")
        if status.status != JobStatus.succeeded:
            reason = f": {ctx.redactor.text(status.error)}" if status.error else ""
            return ToolResult(text=f"Sub-agent {status.child_job_id} ended {status.status.value}{reason}", ok=False)
        output = json.dumps(status.output or {}, ensure_ascii=False, indent=1)
        return ToolResult(
            text=untrusted(f"subagent:{status.child_job_id}", ctx.redactor.text(output)),
            summary=f"sub-agent {status.child_job_id} succeeded",
        )
