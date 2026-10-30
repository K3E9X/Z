"""`AgentBackend` (docs/04 §1): the only interface between the runner and an agent harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from z_common.models.api_internal import JobSpec

from z_runner.events import EventSink
from z_runner.tools.base import ToolSet

StepStatus = Literal["continue", "finished", "confirmation_required", "stuck", "error"]


@dataclass
class StepResult:
    """Outcome of one model turn and its tool calls."""

    status: StepStatus
    tokens_in: int = 0
    tokens_out: int = 0
    final_message: str | None = None
    tool_calls: list[tuple[str, str]] = field(default_factory=list)  # (tool name, canonical args)
    tool_failures: int = 0
    tool_successes: int = 0
    validation_errors: int = 0
    progress: bool = False  # new output (an agent message); repeated successful calls are not progress
    error: str | None = None
    # "llm_unavailable" (transient: rate limit, timeout, unavailable), "llm_budget" (the virtual key budget is
    # spent), "llm_auth", "context_window" or "agent"
    error_class: str | None = None


class AgentBackend(Protocol):
    def start(self, spec: JobSpec, sink: EventSink, tools: ToolSet) -> None: ...
    def step(self) -> StepResult: ...  # one model turn + tool calls
    def checkpoint(self) -> bytes: ...
    def restore(self, blob: bytes) -> None: ...  # before start(): resume from this state
    def inject_message(self, text: str) -> None: ...  # "Guide the agent"
    def stop(self, reason: str) -> None: ...


@runtime_checkable
class SupportsModelSwitch(Protocol):
    """Optional: change the model alias (escalation) or the LLM key (rotation) between steps."""

    def set_llm(self, model: str, api_key: str) -> None: ...
