"""API resources: tasks and triggers (intake), sessions, inference and usage, budgets, providers, analytics,
objectives and assignments (docs/03 §5)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from z_common.models.api_missions import Mission
from z_common.models.base import Id, ModelAlias, Slug, ZModel
from z_common.models.enums import (
    AssignmentKind,
    Autonomy,
    BudgetPeriod,
    DataClass,
    ObjectiveStatus,
    Surface,
    TaskSource,
    TaskStatus,
    TriggerKind,
)
from z_common.models.manifests import ObjectiveManifest

Health = Literal["healthy", "degraded", "down", "unknown"]
BudgetScope = Annotated[str, StringConstraints(pattern=r"^(org|(team|user|playbook|mission|agent|objective):.+)$")]


# ---------------------------------------------------------------- intake


class Task(ZModel):
    id: Id
    source: TaskSource
    external_ref: str | None = None
    title: str
    body_ref: str | None = None
    suggested_agent: str | None = None
    suggested_playbook: str | None = None
    status: TaskStatus
    mission_id: str | None = None
    created_at: datetime


class TaskPatch(ZModel):
    action: Literal["start", "dismiss", "schedule"]
    playbook: str | None = None
    agent: str | None = None
    inputs: dict[str, Any] | None = None
    reason: str | None = None


class Trigger(ZModel):
    id: Id
    kind: TriggerKind
    name: str | None = None
    config: dict[str, Any]
    filter: dict[str, Any]
    playbook: str | None = None
    inputs: dict[str, Any]
    owner_user: str
    auto_start: bool
    enabled: bool
    last_fired_at: datetime | None = None
    webhook_url: str | None = None
    secret: str | None = None
    created_at: datetime


class TriggerCreate(ZModel):
    kind: TriggerKind
    name: str | None = None
    config: dict[str, Any] | None = None
    filter: dict[str, Any] | None = None
    playbook: str | None = None
    inputs: dict[str, Any] | None = None
    auto_start: bool | None = None
    enabled: bool | None = None


class WebhookAccepted(ZModel):
    task_id: str | None = None
    mission_id: str | None = None


# ---------------------------------------------------------------- sessions


class Session(ZModel):
    id: Id
    user: str
    surface: Surface
    title: str
    missions: list[str]
    created_at: datetime
    updated_at: datetime


class SessionCreate(ZModel):
    surface: Surface
    title: str | None = None


class SessionMessage(ZModel):
    role: Literal["user", "assistant", "system"]
    text: str
    ts: datetime
    mission_id: str | None = None


class SessionDetail(Session):
    messages: list[SessionMessage]


class SessionMessageIn(ZModel):
    text: Annotated[str, StringConstraints(min_length=1)]
    autonomy: Autonomy | None = None
    plan_first: bool | None = None
    model: ModelAlias | None = None


class SessionReply(ZModel):
    messages: list[SessionMessage]
    mission: Mission | None = None


# ---------------------------------------------------------------- inference and usage


class InferenceModel(ZModel):
    alias: ModelAlias
    provider: str
    kind: Literal["internal", "external"]
    status: Health
    listed: bool
    replicas_ready: int | None = None
    replicas_desired: int | None = None
    requests_running: float | None = None
    requests_waiting: float | None = None
    ttft_p95_ms: float | None = None
    tokens_per_s: float | None = None
    kv_cache_usage: float | None = None
    context: int | None = None
    tool_call_ok: bool | None = None
    tool_call_parse_error_rate: float | None = None
    last_probe_at: datetime | None = None


class InferenceNode(ZModel):
    name: str
    gpu_util: float | None = None
    gpu_mem_used_ratio: float | None = None
    gpu_temp_c: float | None = None


class GatewayStatus(ZModel):
    ok: bool
    latency_ms: int | None = None
    error: str | None = None


class InferenceHealth(ZModel):
    status: Health
    gateway: GatewayStatus
    prometheus_configured: bool
    models: list[InferenceModel]
    nodes: list[InferenceNode]
    prefix_cache_hit_rate: float | None = None
    preemptions_24h: float | None = None
    tokens_today: int
    checked_at: datetime


class UsageBucket(ZModel):
    ts: datetime
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    requests: int


class UsageSeries(ZModel):
    key: str
    label: str | None = None
    buckets: list[UsageBucket]
    tokens_in: int
    tokens_out: int
    cached_tokens: int
    requests: int
    cost: float | None = None
    cap: int | None = None


class Usage(ZModel):
    group_by: Literal["model", "agent", "mission", "team", "user", "playbook"]
    bucket: Literal["minute", "hour", "day"]
    from_: Annotated[datetime, Field(alias="from")]
    to: datetime
    series: list[UsageSeries]


class Budget(ZModel):
    id: Id
    scope: BudgetScope
    limit: Annotated[int, Field(ge=1)]
    period: BudgetPeriod
    alert_at: Annotated[float, Field(gt=0, le=1)]
    used: int
    hard: bool


class BudgetWrite(ZModel):
    scope: BudgetScope
    limit: Annotated[int, Field(ge=1)]
    period: BudgetPeriod
    alert_at: Annotated[float, Field(gt=0, le=1)] = 0.8
    hard: bool = False


class Provider(ZModel):
    id: Id
    name: str
    kind: Literal["internal", "external"]
    aliases: list[ModelAlias]
    data_classes: list[DataClass]
    enabled: bool


class ProviderWrite(ZModel):
    name: str
    kind: Literal["internal", "external"]
    aliases: list[ModelAlias]
    data_classes: list[DataClass]
    enabled: bool


class HoursSaved(ZModel):
    playbook: str
    completed: int
    baseline_minutes: int
    hours: float


class ConnectorFailures(ZModel):
    connector: str
    failures: int


class TeamTokens(ZModel):
    team: str
    tokens: int
    cost: float | None = None


class Analytics(ZModel):
    from_: Annotated[datetime, Field(alias="from")]
    to: datetime
    hours_saved: list[HoursSaved]
    approval_rate: float | None = None
    edit_rate: float | None = None
    failures_by_connector: list[ConnectorFailures]
    tokens_by_team: list[TeamTokens]
    missions_by_status: dict[str, int]


# ---------------------------------------------------------------- objectives and assignments


class Objective(ObjectiveManifest):
    id: Id
    status: ObjectiveStatus
    last_value: float | None = None
    last_checked_at: datetime | None = None
    trend: list[float] = Field(default_factory=list)
    owner_user: str | None = None


class ObjectivePatch(ZModel):
    title: str | None = None
    metric: dict[str, Any] | None = None
    cadence: Any = None
    allowed_playbooks: list[str] | None = None
    mode: str | None = None
    budget_per_period: dict[str, Any] | None = None
    paused: bool | None = None


class ObjectiveCheck(ZModel):
    ts: datetime
    value: float
    target: float
    status: ObjectiveStatus
    explanation: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    started_missions: list[str] = Field(default_factory=list)


class Assignment(ZModel):
    id: Id
    agent: Slug
    object_kind: AssignmentKind
    object_ref: str
    role: Literal["owner", "watcher"]
    since: datetime


class AssignmentCreate(ZModel):
    object_kind: AssignmentKind
    object_ref: str
    role: Literal["owner", "watcher"] = "watcher"


class ProposalDecision(ZModel):
    reason: str | None = None
