"""API resources: missions, jobs, artifacts, review (actions, questions, suggestions)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from z_common.models.actions import Preview
from z_common.models.base import Id, ModelAlias, OperationId, SemVer, Slug, ZModel
from z_common.models.common import MissionBudget, Problem, RunsAs
from z_common.models.enums import (
    ActionStatus,
    ArtifactKind,
    Autonomy,
    DataClass,
    JobStatus,
    MissionStatus,
    NeedsAttentionReason,
    WaitingReason,
)
from z_common.models.events import Event
from z_common.models.manifests import MissionPlan


class JobCounts(ZModel):
    total: int = 0
    queued: int = 0
    running: int = 0
    needs_attention: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0


class Mission(ZModel):
    id: Id
    title: str
    prompt: str | None = None
    playbook_id: str | None = None
    playbook: str | None = None
    playbook_version: str | None = None
    plan: MissionPlan | None = None
    inputs: dict[str, Any]
    connectors: list[Slug]
    data_class: DataClass
    autonomy: Autonomy
    plan_first: bool
    status: MissionStatus
    waiting_reason: WaitingReason | None = None
    created_by: str
    team: str | None = None
    session_id: str | None = None
    trigger_id: str | None = None
    budget: MissionBudget
    tokens_in: int
    tokens_out: int
    jobs: JobCounts | None = None
    pending_actions: int | None = None
    pending_questions: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: str | None = None


class MissionCreate(ZModel):
    prompt: str | None = None
    playbook: str | None = None
    inputs: dict[str, Any] | None = None
    title: str | None = None
    agents: list[str] | None = None
    connectors: list[Slug] | None = None
    plan: MissionPlan | None = None
    data_class: DataClass | None = None
    autonomy: Autonomy | None = None
    plan_first: bool | None = None
    budget: MissionBudget | None = None
    session_id: str | None = None
    start: bool = True


class DraftPlanRequest(ZModel):
    prompt: str
    agents: list[str] | None = None
    connectors: list[Slug] | None = None


class ParsedRequest(ZModel):
    agents: list[str] | None = None
    connectors: list[str] | None = None
    writes: list[str] | None = None
    sources: list[str] | None = None


class DraftPlan(ZModel):
    plan: MissionPlan
    suggested_playbook: str | None = None
    parsed: ParsedRequest | None = None
    estimated_tokens: int | None = None


class PlanPatch(ZModel):
    plan: MissionPlan


class MissionControl(ZModel):
    reason: str | None = None


class GuideMessage(ZModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    job_id: str | None = None


class TakeOver(ZModel):
    job_id: str
    mode: Literal["read_only", "read_write"] = "read_only"


class TakeOverSession(ZModel):
    job_id: str
    terminal_url: str
    expires_at: datetime
    mode: str


TakeOverOrigin = Literal["staged", "agent", "human"]


class TakeOverFile(ZModel):
    path: str
    size: int
    origin: TakeOverOrigin
    deleted: bool


class TakeOverWorkspace(ZModel):
    job_id: str
    mode: Literal["read_only", "read_write"]
    by: str
    expires_at: datetime
    files: list[TakeOverFile]
    truncated: bool


class TakeOverFileContent(ZModel):
    path: str
    encoding: Literal["utf-8", "base64"]
    content: str
    size: int
    origin: TakeOverOrigin


class TakeOverEdit(ZModel):
    path: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    content: Annotated[str, StringConstraints(max_length=1_500_000)]
    encoding: Literal["utf-8", "base64"] = "utf-8"


class TakeOverFinish(ZModel):
    resume: bool = True


class TakeOverResult(ZModel):
    job_id: str
    edited: list[str]
    restarted: bool
    resumed: bool


class SaveAsPlaybook(ZModel):
    name: Slug
    description: str | None = None
    domain: str | None = None


class PlaybookRun(ZModel):
    inputs: dict[str, Any]
    title: str | None = None
    data_class: DataClass | None = None
    plan_first: bool | None = None
    budget: MissionBudget | None = None
    session_id: str | None = None


class JobError(ZModel):
    class_: Annotated[str, Field(alias="class")]
    message: str


class Job(ZModel):
    id: Id
    mission_id: Id
    step_id: str
    agent: Slug
    agent_version: SemVer
    parent_job_id: str | None = None
    item_key: str | None = None
    input: dict[str, Any]
    status: JobStatus
    needs_attention_reason: NeedsAttentionReason | None = None
    attempt: int
    resume_from_step: int | None = None
    steps_done: int
    model: ModelAlias
    tokens_in: int
    tokens_out: int
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error: JobError | None = None
    output: dict[str, Any] | None = None


class Artifact(ZModel):
    id: Id
    job_id: str | None = None
    mission_id: Id
    kind: ArtifactKind
    name: str
    content_type: str
    size: int
    sha256: str
    data_class: DataClass
    created_at: datetime


class Replay(ZModel):
    mission: Mission
    events: list[Event]
    artifacts: list[Artifact]


class Approval(ZModel):
    by: str
    at: datetime
    step_up: bool
    comment: str | None = None


class JudgeCheck(ZModel):
    name: str
    ok: bool
    detail: str | None = None


class ActionJudgeVerdict(ZModel):
    verdict: Literal["ok", "warn", "block"]
    checks: list[JudgeCheck]


class ProposedAction(ZModel):
    id: Id
    job_id: Id
    mission_id: Id
    agent: Slug
    connector: Slug
    operation: OperationId
    class_: Annotated[Literal["write", "destructive"], Field(alias="class")]
    summary: str
    payload: dict[str, Any]
    preview: Preview
    reversible: bool
    judge_verdict: ActionJudgeVerdict | None = None
    runs_as: RunsAs
    status: ActionStatus
    approvals: list[Approval]
    required_approvals: Annotated[int, Field(ge=1)]
    four_eyes: bool | None = None
    result: dict[str, Any] | None = None
    idempotency_key: str
    superseded_by: str | None = None
    edited_from: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class Question(ZModel):
    id: Id
    job_id: Id
    mission_id: Id
    agent: Slug
    text: str
    options: list[str]
    answer: str | None = None
    answered_by: str | None = None
    answered_at: datetime | None = None
    created_at: datetime


class MissionSuggestion(ZModel):
    id: Id
    objective_id: str | None = None
    agent: Slug
    title: str
    explanation: str
    playbook: str | None = None
    inputs: dict[str, Any]
    estimated_tokens: int | None = None
    has_writes: bool
    created_at: datetime


class ReviewItem(ZModel):
    kind: Literal["action", "question", "suggestion"]
    action: ProposedAction | None = None
    question: Question | None = None
    suggestion: MissionSuggestion | None = None
    mission_title: str
    age_s: int


class ReviewCounts(ZModel):
    all: int
    writes: int
    questions: int
    suggestions: int


class ReviewList(ZModel):
    items: list[ReviewItem]
    counts: ReviewCounts
    next_cursor: str | None


class ApproveRequest(ZModel):
    comment: str | None = None
    exclude: list[str] | None = None


class RejectRequest(ZModel):
    reason: Annotated[str, StringConstraints(min_length=1)]


class EditRequest(ZModel):
    payload: dict[str, Any]
    comment: str | None = None


class BatchApprove(ZModel):
    action_ids: list[Id]
    comment: str | None = None


class BatchItem(ZModel):
    action_id: str
    ok: bool
    status: str | None = None
    error: Problem | None = None


class BatchResult(ZModel):
    results: list[BatchItem]


class AnswerRequest(ZModel):
    answer: Annotated[str, StringConstraints(min_length=1)]
