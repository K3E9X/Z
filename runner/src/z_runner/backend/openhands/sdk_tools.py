"""Adapter: a backend-neutral `ZTool` as an OpenHands SDK `ToolDefinition`.

The model sees the tool's own JSON Schema (plus the optional `summary` and `security_risk` fields the SDK adds).
Arguments are validated strictly with jsonschema in `action_from_arguments`; the SDK turns the ValueError into
an `AgentErrorEvent` sent back to the model, and the structured errors are kept for the `validation_error`
event. Execution errors never escape: they become error observations.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Self

from openhands.sdk.llm import TextContent
from openhands.sdk.security import risk
from openhands.sdk.tool import Action, Observation, ToolAnnotations, ToolDefinition, ToolExecutor
from pydantic import Field
from z_common.models.common import FieldError
from z_common.models.enums import JudgeVerdict

from z_runner.client import ApiError
from z_runner.tools.base import ArgumentError, ToolContext, ToolResult, ZTool, corrective_message, validate_args

if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation

log = logging.getLogger("z_runner.backend.openhands")

SUMMARY_FIELD = "summary"


class ZToolAction(Action):
    """Raw, schema-validated arguments of a Z tool call."""

    data: dict[str, Any] = Field(default_factory=dict)
    # Declared so the SDK never pops a tool's own `summary` argument (e.g. propose_action).
    summary: str | None = None


class ZToolObservation(Observation):
    """Tool output plus the fields of the Z `tool_result` event."""

    z_ok: bool = True
    z_summary: str = ""
    z_judge: str = JudgeVerdict.clean.value
    z_artifact_ref: str | None = None
    z_duration_ms: int = 0


class ValidationRecorder:
    """Structured argument errors, by tool name, waiting for the matching AgentErrorEvent."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, list[FieldError]] = {}

    def put(self, tool: str, errors: list[FieldError]) -> None:
        with self._lock:
            self._pending[tool] = errors

    def pop(self, tool: str) -> list[FieldError] | None:
        with self._lock:
            return self._pending.pop(tool, None)


class ZToolExecutor(ToolExecutor[ZToolAction, ZToolObservation]):
    def __init__(self, tool: ZTool, context: ToolContext) -> None:
        self.tool = tool
        self.context = context

    def __call__(self, action: ZToolAction, conversation: LocalConversation | None = None) -> ZToolObservation:
        started = time.monotonic()
        try:
            result = self.tool.run(dict(action.data), self.context)
        except ArgumentError as exc:
            self.context.sink.emit(
                "validation_error",
                {"where": "tool_args", "errors": [e.model_dump(mode="json") for e in exc.errors]},
            )
            result = ToolResult(
                text=corrective_message(self.tool.name, exc.errors), ok=False, summary="invalid arguments"
            )
        except ApiError as exc:
            log.warning("tool %s: internal API error: %s", self.tool.name, exc)
            result = ToolResult(
                text=f"The platform could not complete {self.tool.name}: {exc}", ok=False, summary="platform error"
            )
        except Exception as exc:  # a tool bug must not crash the job
            log.exception("tool %s failed", self.tool.name)
            result = ToolResult(
                text=f"Tool {self.tool.name} failed: {type(exc).__name__}", ok=False, summary="tool error"
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        text = self.context.redactor.text(result.text)
        summary = self.context.redactor.text(
            result.summary or text.strip().splitlines()[0][:300] if text.strip() else ""
        )
        return ZToolObservation(
            content=[TextContent(text=text)],
            is_error=not result.ok,
            z_ok=result.ok,
            z_summary=summary[:2000],
            z_judge=result.judge.value,
            z_artifact_ref=result.artifact_ref,
            z_duration_ms=duration_ms,
        )


class ZSdkTool(ToolDefinition[ZToolAction, ZToolObservation]):
    """One Z tool as seen by the SDK. Registered as an instance (see OpenHandsBackend)."""

    tool_name: str
    input_schema: dict[str, Any]
    z_kind: str

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.tool_name

    @classmethod
    def create(cls, *args: Any, **kwargs: Any) -> Sequence[Self]:
        raise NotImplementedError("Z tools are registered as instances bound to one job")

    @classmethod
    def build(cls, tool: ZTool, context: ToolContext, recorder: ValidationRecorder) -> ZSdkTool:
        instance = cls(
            description=tool.description,
            action_type=ZToolAction,
            observation_type=ZToolObservation,
            annotations=ToolAnnotations(title=tool.name, readOnlyHint=tool.read_only),
            executor=ZToolExecutor(tool, context),
            tool_name=tool.name,
            input_schema=tool.input_schema,
            z_kind=tool.kind,
        )
        instance._recorder = recorder
        return instance

    _recorder: ValidationRecorder | None = None

    def _declares_summary(self) -> bool:
        return SUMMARY_FIELD in self.input_schema.get("properties", {})

    def action_from_arguments(self, arguments: dict[str, Any]) -> ZToolAction:
        args = dict(arguments)
        if not self._declares_summary():
            args.pop(SUMMARY_FIELD, None)
        errors = validate_args(self.input_schema, args)
        if errors:
            if self._recorder is not None:
                self._recorder.put(self.tool_name, errors)
            raise ValueError(corrective_message(self.tool_name, errors))
        return ZToolAction(data=args)

    def _get_tool_schema(
        self,
        add_security_risk_prediction: bool = False,
        action_type: type[Any] | None = None,
    ) -> dict[str, Any]:
        schema = copy.deepcopy(self.input_schema)
        properties = schema.setdefault("properties", {})
        if add_security_risk_prediction and not (self.annotations and self.annotations.readOnlyHint):
            properties["security_risk"] = {
                "type": "string",
                "description": "Your assessment of the safety risk of this action.",
                "enum": [r.value for r in risk.SecurityRisk],
            }
        if SUMMARY_FIELD not in properties:
            properties[SUMMARY_FIELD] = {
                "type": "string",
                "description": "Optional: about ten words describing what this call does.",
            }
        return schema
