"""Runner <-> z-server protocol (contracts/README.md "Runner protocol"): job spec, control, events,
checkpoints, built-ins (propose_action, ask_human, search_memory, spawn_subagent, report_objective), completion."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from z_common.models.base import Id, ModelAlias, OperationId, SemVer, Slug, ZModel
from z_common.models.common import Limits
from z_common.models.enums import (
    ActionStatus,
    ArtifactKind,
    DataClass,
    JobStatus,
    JudgeVerdict,
    NeedsAttentionReason,
    WorkspaceMode,
)
from z_common.models.events import EventIn
from z_common.models.manifests import AgentProfile

TOOL_NAME = re.compile(r"^[a-z0-9_]+(__[a-z0-9_]+)+$")


def tool_name(operation_id: str) -> str:
    """Model-facing tool name of a gateway operation: dots become double underscores."""
    return operation_id.replace(".", "__")


def operation_of(name: str) -> str:
    return name.replace("__", ".")


class SkillResource(ZModel):
    path: str
    content_b64: str


class SkillBundle(ZModel):
    name: Slug
    version: SemVer
    content: str
    resources: list[SkillResource] | None = None


class MemorySnippet(ZModel):
    id: Id
    scope: Literal["org", "team", "agent"]
    text: str


class GatewayTool(ZModel):
    name: Annotated[str, StringConstraints(pattern=TOOL_NAME.pattern)]
    operation: OperationId
    description: str
    input_schema: dict[str, Any]


class WriteTarget(ZModel):
    operation: OperationId
    connector: Slug
    class_: Annotated[Literal["write", "destructive"], Field(alias="class")]
    input_schema: dict[str, Any]
    description: str | None = None


class OutputSpec(ZModel):
    schema_: Annotated[dict[str, Any], Field(alias="schema")]
    max_repair_attempts: int


class WorkspaceSpec(ZModel):
    mode: WorkspaceMode
    archive_url: str | None = None


LocalTool = Literal["read", "edit", "terminal", "code", "documents", "browser"]
Builtin = Literal["propose_action", "ask_human", "spawn_subagent", "search_memory", "report_objective"]


class ToolSpec(ZModel):
    local: list[LocalTool]
    builtins: list[Builtin]
    gateway: list[GatewayTool]


class LlmSpec(ZModel):
    base_url: str
    api_key: str


class JobSpec(ZModel):
    job_id: Id
    mission_id: Id
    step_id: str
    attempt: Annotated[int, Field(ge=1)]
    resume_from_step: int | None = None
    agent: AgentProfile
    skills: list[SkillBundle]
    memory: list[MemorySnippet]
    prompt: str
    input: dict[str, Any]
    output: OutputSpec | None = None
    writes: list[WriteTarget]
    workspace: WorkspaceSpec
    tools: ToolSpec
    limits: Limits
    model: ModelAlias
    escalate_model: ModelAlias | None = None
    llm: LlmSpec
    gateway_url: str
    server_url: str
    session_token: str
    deadline: datetime
    checkpoint_b64: str | None = None
    objective_id: str | None = None
    untrusted_notice: str | None = None


class EventBatch(ZModel):
    events: list[EventIn]


class ControlMessage(ZModel):
    id: str
    text: str
    by: str | None = None


class Answer(ZModel):
    question_id: str
    answer: str
    answered_by: str


class ActionState(ZModel):
    action_id: str
    status: ActionStatus


class JobControl(ZModel):
    state: Literal["run", "pause", "cancel"]
    reason: str | None = None
    messages: list[ControlMessage]
    answers: list[Answer]
    actions: list[ActionState]
    llm_api_key: str | None = None


class CheckpointIn(ZModel):
    step: Annotated[int, Field(ge=0)]
    blob_b64: str
    tokens_in: int | None = None
    tokens_out: int | None = None


class ActionIn(ZModel):
    connector: Slug
    operation: OperationId
    payload: dict[str, Any]
    summary: str


class QuestionIn(ZModel):
    text: Annotated[str, StringConstraints(min_length=1)]
    options: list[str] | None = None


class MemorySearch(ZModel):
    query: str
    top_k: Annotated[int, Field(ge=1, le=50)] | None = None


class SubagentIn(ZModel):
    agent: Slug
    input: dict[str, Any] | None = None
    prompt: str
    budget_share: Annotated[float, Field(gt=0, le=1)] | None = None


class SubagentStatus(ZModel):
    child_job_id: str
    status: JobStatus
    output: dict[str, Any] | None = None
    error: str | None = None


class SuggestedMission(ZModel):
    title: str
    playbook: str | None = None
    inputs: dict[str, Any]


class ObjectiveReport(ZModel):
    objective_id: str
    value: float
    explanation: str
    suggestions: list[SuggestedMission] | None = None


class JobError(ZModel):
    class_: Annotated[str, Field(alias="class")]
    message: str


class JobComplete(ZModel):
    status: Literal["succeeded", "failed", "needs_attention", "budget_exceeded", "timed_out", "cancelled"]
    output: dict[str, Any] | None = None
    report_artifact: str | None = None
    needs_attention_reason: NeedsAttentionReason | None = None
    error: JobError | None = None
    tokens_in: int
    tokens_out: int
    steps: int


class WorkspaceFile(ZModel):
    path: str
    size: int
    sha256: str
    judge: JudgeVerdict


class WorkspaceManifest(ZModel):
    files: list[WorkspaceFile]
    flagged: list[str]
    repo: str | None = None
    commit: str | None = None


class DataClassRaise(ZModel):
    to: DataClass
    cause: Literal["tool_result", "input", "judge"]
    detail: str | None = None


class ArtifactMeta(ZModel):
    """Form fields of POST /internal/jobs/{id}/artifacts (with `file`)."""

    kind: ArtifactKind
    name: str
