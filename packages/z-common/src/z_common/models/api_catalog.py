"""API resources: agents, skills, playbooks, connectors and operations (openapi components)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from z_common.models.base import Handle, Id, ModelAlias, OperationId, SemVer, Slug, ZModel
from z_common.models.common import Avatar, Limits, MemoryConfig, MissionBudget, Persona
from z_common.models.enums import (
    Autonomy,
    ConnectorHealth,
    ConnectorKind,
    CredentialPurpose,
    CredentialScope,
    DataClass,
    OperationClass,
    ReviewStatus,
)
from z_common.models.manifests import (
    AgentProfile,
    BrowserFlow,
    BrowserStep,
    CliSettings,
    ConnectorManifest,
    DeclaredOperation,
    McpServer,
    PlaybookInputs,
    PlaybookTrigger,
    Step,
    ToolRef,
)


class EvalsSummary(ZModel):
    passed: int
    total: int
    model: str | None = None
    run_id: str | None = None


class Agent(AgentProfile):
    """Agent profile plus server fields."""

    id: Id
    owner_team: str | None = None
    status: Literal["working", "available", "disabled"]
    pack: str | None = None
    current_missions: list[str] | None = None
    evals_summary: EvalsSummary | None = None
    created_at: datetime
    updated_at: datetime


class AgentFromTemplate(ZModel):
    template: Slug
    display_name: Annotated[str, Field(min_length=1, max_length=64)]
    handle: Handle
    avatar: Avatar | None = None
    owner_team: str | None = None


class AgentPatch(ZModel):
    """Any subset of the profile; `name` never changes."""

    name: Slug | None = None
    display_name: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    handle: Handle | None = None
    avatar: Avatar | None = None
    version: SemVer | None = None
    role: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    instructions: Annotated[str, Field(max_length=20000)] | None = None
    persona: Persona | None = None
    template: str | None = None
    model: ModelAlias | None = None
    escalate_model: ModelAlias | None = None
    skills: list[Slug] | None = None
    tools: list[ToolRef] | None = None
    memory: MemoryConfig | None = None
    autonomy: Autonomy | None = None
    limits: Limits | None = None
    evals: str | None = None
    visibility: Literal["team", "org"] | None = None
    change_note: str | None = None
    owner_team: str | None = None


class AgentDuplicate(ZModel):
    display_name: Annotated[str, Field(min_length=1, max_length=64)]
    handle: Handle
    avatar: Avatar | None = None


class AgentVersion(ZModel):
    version: SemVer
    created_at: datetime
    created_by: str | None = None
    change_note: str | None = None
    profile: AgentProfile


class SkillChange(ZModel):
    kind: Literal["created", "edited", "learned", "rolled_back"]
    at: datetime
    by: str | None = None
    proposal_id: str | None = None


class Skill(ZModel):
    id: Id
    name: Slug
    description: str
    version: SemVer
    origin: Literal["pack", "learned", "user"]
    pack: str | None = None
    path: str | None = None
    checksum: str
    evals: str | None = None
    owner: str | None = None
    agents: list[str]
    content: str | None = None
    last_change: SkillChange | None = None
    pending_proposal_id: str | None = None


class SkillVersion(ZModel):
    version: SemVer
    checksum: str
    created_at: datetime
    created_by: str | None = None
    origin: str
    proposal_id: str | None = None


class SkillRollback(ZModel):
    to_version: SemVer
    reason: str | None = None


class Playbook(ZModel):
    """Playbook manifest plus server fields."""

    name: Slug
    version: SemVer
    domain: str | None = None
    description: str
    inputs: PlaybookInputs | None = None
    team: list[Slug]
    connectors: list[Slug] = Field(default_factory=list)
    steps: list[Step]
    trigger: PlaybookTrigger | None = None
    autonomy: Autonomy | None = None
    four_eyes: bool = False
    budget: MissionBudget | None = None
    data_class: DataClass | None = None
    baseline_minutes: int | None = None
    id: Id
    pack: str | None = None
    owner_team: str | None = None
    usage_count: int
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConnectorHealthInfo(ZModel):
    status: ConnectorHealth
    latency_p50_ms: int | None = None
    latency_p95_ms: int | None = None
    error_rate: float | None = None
    last_success_at: datetime | None = None
    checked_at: datetime | None = None
    detail: str | None = None


class CredentialInfo(ZModel):
    id: Id
    purpose: CredentialPurpose
    scope: CredentialScope
    backend: str
    ref: str
    identity: str | None = None
    scopes: list[str] | None = None
    rotated_at: datetime | None = None
    expires_at: datetime | None = None


class Connector(ZModel):
    id: Id
    name: Slug
    kind: ConnectorKind
    display_name: str
    base_url: str | None = None
    data_class: DataClass
    review_status: ReviewStatus
    enabled: bool
    health: ConnectorHealthInfo
    operations_total: int
    operations_exposed: int
    used_by: list[str]
    credentials: list[CredentialInfo] | None = None
    rate_limit: dict[str, Any] | None = None
    pack: str | None = None
    manifest: ConnectorManifest
    created_at: datetime
    updated_at: datetime | None = None


class Operation(ZModel):
    id: OperationId
    connector: Slug
    source_ref: str
    description: str | None = None
    class_: Annotated[OperationClass, Field(alias="class")]
    classifier_class: OperationClass
    exposed: bool
    proposed_exposed: bool
    rate_limit: dict[str, Any] | None = None
    input_schema: dict[str, Any]
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class OperationPatch(ZModel):
    class_: Annotated[OperationClass | None, Field(alias="class")] = None
    exposed: bool | None = None
    rate_limit: dict[str, Any] | None = None


class ExposureDecision(ZModel):
    id: OperationId
    exposed: bool


class ApproveExposure(ZModel):
    operations: list[ExposureDecision]


class ConnectorPatch(ZModel):
    manifest: ConnectorManifest | None = None
    enabled: bool | None = None


class ConnectorTestRequest(ZModel):
    operation: str | None = None
    params: dict[str, Any] | None = None
    credential_id: str | None = None


class ConnectorTestResult(ZModel):
    ok: bool
    latency_ms: int
    status_code: int | None = None
    detail: str
    sample: Any = None


class ConnectorImport(ZModel):
    source: Literal["openapi", "graphql", "mcp", "docs", "browser", "cli"]
    name: Slug
    display_name: str | None = None
    base_url: str | None = None
    spec_url: str | None = None
    spec: dict[str, Any] | str | None = None
    mcp: McpServer | None = None
    docs: str | None = None
    flows: dict[str, BrowserFlow] | None = None
    declared: list[DeclaredOperation] | None = None
    cli: CliSettings | None = None
    data_class: DataClass | None = None


class ConnectorDraft(ZModel):
    manifest: ConnectorManifest
    operations: list[Operation]
    warnings: list[str] = Field(default_factory=list)


class BrowserRecording(ZModel):
    connector: Slug
    start_url: str
    name: str
    purpose: Literal["read", "write"] = "read"


class BrowserRecordingSession(ZModel):
    id: Id
    viewer_url: str
    expires_at: datetime


class RecordingViewport(ZModel):
    width: int
    height: int


class BrowserRecordingState(ZModel):
    id: Id
    connector: Slug
    name: str
    purpose: Literal["read", "write"]
    url: str
    title: str
    steps: list[BrowserStep]
    blocked_hosts: list[str]
    viewport: RecordingViewport
    expires_at: datetime


class BrowserRecordingInput(ZModel):
    kind: Literal["click", "type", "credential", "press", "goto", "extract", "assert", "screenshot", "undo"]
    x: float | None = None
    y: float | None = None
    text: Annotated[str, StringConstraints(max_length=4000)] | None = None
    arg: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")] | None = None
    field: Literal["username", "password"] | None = None
    key: Literal["Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "Space"] | None = None
    url: Annotated[str, StringConstraints(max_length=2000)] | None = None
    what: Literal["text", "table", "link"] | None = None
    name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")] | None = None


class BrowserRecordingDraft(ZModel):
    flow: BrowserFlow
    blocked_hosts: list[str]
