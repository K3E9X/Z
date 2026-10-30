"""events.schema.json: the event envelope and one payload model per event type."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from z_common.models.base import Id, ZModel
from z_common.models.common import FieldError
from z_common.models.enums import ActionStatus, ArtifactKind, DataClass, JudgeVerdict, ObjectiveStatus, SignalKind
from z_common.models.manifests import PlanStep

EventType = Literal[
    "status",
    "plan",
    "message",
    "thought",
    "tool_call",
    "tool_result",
    "terminal",
    "file_edit",
    "browser",
    "denied",
    "validation_error",
    "usage",
    "artifact",
    "question",
    "question_answered",
    "action_proposed",
    "action_status",
    "subagent",
    "checkpoint",
    "data_class",
    "judge",
    "learning_signal",
    "objective_update",
]
NonNeg = Annotated[int, Field(ge=0)]


class StatusPayload(ZModel):
    entity: Literal["mission", "job", "step"]
    id: str | None = None
    from_: Annotated[str | None, Field(alias="from")]
    to: str
    reason: str | None = None


class PlanPayload(ZModel):
    steps: list[PlanStep]
    writes: list[str] | None = None


class MessagePayload(ZModel):
    role: Literal["user", "agent", "system"]
    text: str
    by: str | None = None


class ThoughtPayload(ZModel):
    text: str


class ToolCallPayload(ZModel):
    call_id: str
    tool: str
    operation: str | None = None
    args: dict[str, Any]


class ToolResultPayload(ZModel):
    call_id: str
    ok: bool
    summary: str
    artifact_ref: str | None = None
    duration_ms: NonNeg
    judge: JudgeVerdict


class TerminalPayload(ZModel):
    command: str
    exit_code: int | None = None
    output_ref: str | None = None
    output_tail: str | None = None


class FileEditPayload(ZModel):
    path: str
    diff_ref: str | None = None
    added: NonNeg
    removed: NonNeg


class BrowserPayload(ZModel):
    url: str
    action: str
    screenshot_ref: str | None = None


class DeniedPayload(ZModel):
    what: str
    rule: str
    detail: str | None = None


class ValidationErrorPayload(ZModel):
    where: Literal["tool_args", "output"]
    errors: list[FieldError]
    attempt: NonNeg | None = None


class UsagePayload(ZModel):
    model: str
    tokens_in: NonNeg
    tokens_out: NonNeg
    cached_tokens: NonNeg | None = None
    latency_ms: NonNeg | None = None


class ArtifactPayload(ZModel):
    artifact_id: str
    kind: ArtifactKind
    name: str


class QuestionPayload(ZModel):
    question_id: str
    text: str
    options: list[str] | None = None


class QuestionAnsweredPayload(ZModel):
    question_id: str
    answer: str
    answered_by: str


class ActionProposedPayload(ZModel):
    action_id: str
    connector: str
    operation: str
    class_: Annotated[Literal["write", "destructive"], Field(alias="class")]
    summary: str


class ActionStatusPayload(ZModel):
    action_id: str
    from_: Annotated[ActionStatus, Field(alias="from")]
    to: ActionStatus
    by: str | None = None
    result: dict[str, Any] | None = None


class SubagentPayload(ZModel):
    child_job_id: str
    agent: str
    input_summary: str


class CheckpointPayload(ZModel):
    step: NonNeg
    size: NonNeg


class DataClassPayload(ZModel):
    from_: Annotated[DataClass, Field(alias="from")]
    to: DataClass
    cause: Literal["tool_result", "input", "judge", "user", "playbook"]
    detail: str | None = None


class JudgePayload(ZModel):
    subject: Literal["tool_result", "staged_input", "proposed_action", "mission_prompt", "channel_message"]
    ref: str | None = None
    verdict: JudgeVerdict
    categories: list[str] | None = None
    reason: str | None = None


class LearningSignalPayload(ZModel):
    signal_id: str
    kind: SignalKind


class ObjectiveUpdatePayload(ZModel):
    objective_id: str
    value: float
    target: float
    status: ObjectiveStatus


PAYLOADS: dict[str, type[ZModel]] = {
    "status": StatusPayload,
    "plan": PlanPayload,
    "message": MessagePayload,
    "thought": ThoughtPayload,
    "tool_call": ToolCallPayload,
    "tool_result": ToolResultPayload,
    "terminal": TerminalPayload,
    "file_edit": FileEditPayload,
    "browser": BrowserPayload,
    "denied": DeniedPayload,
    "validation_error": ValidationErrorPayload,
    "usage": UsagePayload,
    "artifact": ArtifactPayload,
    "question": QuestionPayload,
    "question_answered": QuestionAnsweredPayload,
    "action_proposed": ActionProposedPayload,
    "action_status": ActionStatusPayload,
    "subagent": SubagentPayload,
    "checkpoint": CheckpointPayload,
    "data_class": DataClassPayload,
    "judge": JudgePayload,
    "learning_signal": LearningSignalPayload,
    "objective_update": ObjectiveUpdatePayload,
}


def parse_payload(event_type: str, payload: dict[str, Any]) -> ZModel:
    """Validate a payload for its event type (raises pydantic.ValidationError)."""
    return PAYLOADS[event_type].model_validate(payload)


class EventIn(ZModel):
    """What a runner posts; z-server sets mission_id, job_id and seq."""

    ts: datetime
    type: EventType
    step_id: str | None = None
    payload: dict[str, Any]

    def typed_payload(self) -> ZModel:
        return parse_payload(self.type, self.payload)


class Event(ZModel):
    mission_id: Id
    job_id: Id | None
    seq: NonNeg
    ts: datetime
    type: EventType
    agent: str | None = None
    step_id: str | None = None
    payload: dict[str, Any]

    def typed_payload(self) -> ZModel:
        return parse_payload(self.type, self.payload)
