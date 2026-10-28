"""Translate OpenHands SDK events into Z events (contracts/events.schema.json).

| SDK event | Z event(s) |
|---|---|
| MessageEvent (agent) | `message` (role agent), `thought` for reasoning content |
| MessageEvent (environment: SDK nudges) | `message` (role system) |
| ActionEvent | `thought` (text and reasoning around the call), `tool_call` (args redacted) |
| ActionEvent `finish` / `think` | `message` (role agent) / `thought` |
| ObservationEvent | `tool_result` (ok, summary, duration, judge from the tool) |
| AgentErrorEvent | `validation_error` (where tool_args) or `denied` (operation outside the allowlist), then `tool_result` ok=false |
| UserRejectObservation | `tool_result` ok=false |
| SecurityAnalysisEvent with a risk needing confirmation | `denied` (rule guardrail_confirm) |

Other SDK events (system prompt, pause, condensation, streaming, state updates) have no Z equivalent. The
`MaxIterationsReached` error the runner provokes to stop after one step (`max_iteration_per_run=1`) is ignored.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Event,
    MessageEvent,
    ObservationEvent,
    SecurityAnalysisEvent,
    UserRejectObservation,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import TextContent
from openhands.sdk.security.risk import SecurityRisk
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.tool.builtins.think import ThinkAction
from z_common.models.api_internal import TOOL_NAME, operation_of

from z_runner.backend.openhands.sdk_tools import ValidationRecorder, ZToolObservation
from z_runner.events import EventSink
from z_runner.guardrails import canonical_args
from z_runner.tools.base import ToolSet

MAX_ITERATIONS_CODE = "MaxIterationsReached"
_SDK_TOOLS = frozenset({"finish", "think"})
_UNKNOWN_TOOL = re.compile(r"^Tool '([^']+)' not found")


def _text(contents: Any) -> str:
    return "\n".join(c.text for c in contents or [] if isinstance(c, TextContent) and c.text).strip()


@dataclass
class StepStats:
    tool_calls: list[tuple[str, str]] = field(default_factory=list)
    successes: int = 0
    failures: int = 0
    validation_errors: int = 0
    final_message: str | None = None
    agent_messages: int = 0
    error: str | None = None
    confirmation_denied: bool = False


class EventTranslator:
    def __init__(self, sink: EventSink, tools: ToolSet, recorder: ValidationRecorder) -> None:
        self._sink = sink
        self._tools = tools
        self._recorder = recorder
        self._lock = threading.Lock()
        self._started: dict[str, float] = {}
        self._actions: dict[str, ActionEvent] = {}
        self.stats = StepStats()

    def begin_step(self) -> StepStats:
        with self._lock:
            previous, self.stats = self.stats, StepStats()
            return previous

    # ------------------------------------------------------------------ dispatch
    def on_event(self, event: Event) -> None:
        if isinstance(event, ActionEvent):
            self._action(event)
        elif isinstance(event, ObservationEvent):
            self._observation(event)
        elif isinstance(event, AgentErrorEvent):
            self._agent_error(event)
        elif isinstance(event, UserRejectObservation):
            self._result(event.tool_call_id, event.tool_name, ok=False, summary=f"rejected: {event.rejection_reason}")
        elif isinstance(event, MessageEvent):
            self._message(event)
        elif isinstance(event, SecurityAnalysisEvent):
            self._security(event)
        elif isinstance(event, ConversationErrorEvent) and event.code != MAX_ITERATIONS_CODE:
            self.stats.error = f"{event.code}: {event.detail}"

    # ------------------------------------------------------------------ handlers
    def _message(self, event: MessageEvent) -> None:
        if event.source == "user":
            return  # the task, guidance and repair messages come from the platform or a person
        text = _text(event.llm_message.content)
        if event.llm_message.reasoning_content:
            self._sink.emit("thought", {"text": event.llm_message.reasoning_content})
        if not text:
            return
        if event.source == "agent":
            self._sink.emit("message", {"role": "agent", "text": text})
            self.stats.final_message = text
            self.stats.agent_messages += 1
        else:
            self._sink.emit("message", {"role": "system", "text": text})

    def _action(self, event: ActionEvent) -> None:
        if event.reasoning_content:
            self._sink.emit("thought", {"text": event.reasoning_content})
        narration = _text(event.thought)
        if narration:
            self._sink.emit("thought", {"text": narration})
        action = event.action
        if isinstance(action, FinishAction):
            if action.message:
                self._sink.emit("message", {"role": "agent", "text": action.message})
                self.stats.final_message = action.message
                self.stats.agent_messages += 1
            return
        if isinstance(action, ThinkAction):
            self._sink.emit("thought", {"text": action.thought})
            return
        if event.tool_name in _SDK_TOOLS:
            return
        args = self._arguments(event)
        tool = self._tools.get(event.tool_name)
        kind = tool.kind if tool else event.tool_name
        payload: dict[str, Any] = {"call_id": event.tool_call_id, "tool": kind, "args": args}
        operation = tool.operation(args) if tool else None
        if operation:
            payload["operation"] = operation
        with self._lock:
            self._started[event.tool_call_id] = time.monotonic()
            self._actions[event.id] = event
        self._sink.emit("tool_call", payload)
        self.stats.tool_calls.append((event.tool_name, canonical_args(args)))

    @staticmethod
    def _arguments(event: ActionEvent) -> dict[str, Any]:
        if event.action is not None and hasattr(event.action, "data"):
            data = event.action.data
            if isinstance(data, dict):
                return dict(data)
        try:
            parsed = json.loads(event.tool_call.arguments)
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(parsed, dict) or parsed.get("_openhands_malformed_tool_call"):
            return {}
        parsed.pop("summary", None)
        parsed.pop("security_risk", None)
        return parsed

    def _duration(self, call_id: str) -> int:
        with self._lock:
            started = self._started.pop(call_id, None)
        return int((time.monotonic() - started) * 1000) if started is not None else 0

    def _result(
        self,
        call_id: str,
        tool_name: str,
        *,
        ok: bool,
        summary: str,
        judge: str = "clean",
        artifact_ref: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if tool_name in _SDK_TOOLS:
            return
        payload: dict[str, Any] = {
            "call_id": call_id,
            "ok": ok,
            "summary": summary,
            "duration_ms": max(0, duration_ms if duration_ms is not None else self._duration(call_id)),
            "judge": judge,
        }
        if artifact_ref:
            payload["artifact_ref"] = artifact_ref
        self._sink.emit("tool_result", payload)
        if ok:
            self.stats.successes += 1
        else:
            self.stats.failures += 1

    def _observation(self, event: ObservationEvent) -> None:
        observation = event.observation
        self._duration(event.tool_call_id)
        if isinstance(observation, ZToolObservation):
            self._result(
                event.tool_call_id,
                event.tool_name,
                ok=observation.z_ok,
                summary=observation.z_summary,
                judge=observation.z_judge,
                artifact_ref=observation.z_artifact_ref,
                duration_ms=observation.z_duration_ms,
            )
        else:
            text = _text(observation.content)
            self._result(event.tool_call_id, event.tool_name, ok=not observation.is_error, summary=text[:300])

    def _agent_error(self, event: AgentErrorEvent) -> None:
        match = _UNKNOWN_TOOL.match(event.error)
        if match:
            name = match.group(1)
            if TOOL_NAME.fullmatch(name):
                operation = operation_of(name)
                rule = "write_requires_proposal" if operation in self._tools.write_operations else "not_in_allowlist"
                detail = (
                    f"{operation} is a write of this step: call propose_action instead"
                    if rule == "write_requires_proposal"
                    else f"{operation} is not exposed to this job"
                )
                self._sink.emit("denied", {"what": operation, "rule": rule, "detail": detail})
            else:
                self._sink.emit(
                    "validation_error",
                    {"where": "tool_args", "errors": [{"path": "name", "message": f"unknown tool {name!r}"}]},
                )
                self.stats.validation_errors += 1
        elif event.error.startswith("Error validating tool"):
            errors = self._recorder.pop(event.tool_name)
            listed = (
                [e.model_dump(mode="json") for e in errors]
                if errors
                else [{"path": "(arguments)", "message": event.error[:2000]}]
            )
            self._sink.emit("validation_error", {"where": "tool_args", "errors": listed})
            self.stats.validation_errors += 1
        self._result(event.tool_call_id, event.tool_name, ok=False, summary=event.error[:300])

    def _security(self, event: SecurityAnalysisEvent) -> None:
        risky = [action_id for action_id, level in event.risks.items() if level == SecurityRisk.HIGH]
        for action_id in risky:
            with self._lock:
                action = self._actions.get(action_id)
            what = action.tool_name if action else action_id
            self._sink.emit(
                "denied",
                {
                    "what": what,
                    "rule": "guardrail_confirm",
                    "detail": f"the harness security analyzer rated this action HIGH risk ({event.analyzer}); it needs a human",
                },
            )
            self.stats.confirmation_denied = True
