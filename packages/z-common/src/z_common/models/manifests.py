"""Pack manifests: agent, playbook (steps, trigger), mission plan, connector, pack, skill front matter, objective, eval case."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from z_common.models.base import (
    Handle,
    Json,
    LocalOperationId,
    ModelAlias,
    OperationId,
    SecretRef,
    SemVer,
    Slug,
    StepId,
    WriteOperationId,
    ZModel,
)
from z_common.models.common import Avatar, Limits, MemoryConfig, MissionBudget, Persona
from z_common.models.enums import (
    AssignmentKind,
    Autonomy,
    ConnectorKind,
    CredentialScope,
    DataClass,
    ObjectiveMode,
    ObjectiveStatus,
    OperationClass,
    StepStatus,
    TriggerKind,
    WorkspaceMode,
)

ToolRef = Annotated[
    str,
    StringConstraints(
        pattern=r"^(read|edit|terminal|browser|code|documents|spawn_subagent|ask_human|search_memory|report_objective"
        r"|gateway:(@read|[a-z0-9_]+(\.([a-z0-9_]+|\*))+))$"
    ),
]
LOCAL_TOOLS = frozenset({"read", "edit", "terminal", "browser", "code", "documents"})
BUILTIN_TOOLS = frozenset({"spawn_subagent", "ask_human", "search_memory", "report_objective"})
IMPLICIT_BUILTINS = frozenset({"ask_human", "search_memory", "report_objective", "propose_action"})


# ---------------------------------------------------------------- agent
class AgentProfile(ZModel):
    """agent.schema.json"""

    name: Slug
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    handle: Handle
    avatar: Avatar | None = None
    version: SemVer
    role: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    instructions: Annotated[str, StringConstraints(max_length=20000)] | None = None
    persona: Persona | None = None
    template: str | None = None
    model: ModelAlias
    escalate_model: ModelAlias | None = None
    skills: list[Slug]
    tools: list[ToolRef]
    memory: MemoryConfig | None = None
    autonomy: Autonomy
    limits: Limits
    evals: str | None = None
    visibility: Literal["team", "org"] = "team"

    @property
    def gateway_patterns(self) -> list[str]:
        return [t.removeprefix("gateway:") for t in self.tools if t.startswith("gateway:")]


# ---------------------------------------------------------------- playbook
class Fanout(ZModel):
    over: Annotated[str, StringConstraints(pattern=r"^steps\.[a-z0-9_-]+\.(output(\.[A-Za-z0-9_]+)+|outputs)$")]
    concurrency: Annotated[int, Field(ge=1, le=64)] = 8
    when: str | None = None


class StepOutput(ZModel):
    schema_: Annotated[str, Field(alias="schema")]
    max_repair_attempts: Annotated[int, Field(ge=0, le=5)] = 2


class StepCore(ZModel):
    id: StepId
    title: str | None = None
    agent: Slug
    prompt: Annotated[str, StringConstraints(min_length=1)] | None = None
    depends_on: list[StepId] = Field(default_factory=list)
    fanout: Fanout | None = None
    inputs_from: (
        Annotated[str, StringConstraints(pattern=r"^steps\.[a-z0-9_-]+\.(output(\.[A-Za-z0-9_]+)*|outputs)$")] | None
    ) = None
    when: str | None = None
    output: StepOutput | None = None
    writes: list[WriteOperationId] = Field(default_factory=list)
    workspace: WorkspaceMode = WorkspaceMode.none
    repo: str | None = None
    credentials: CredentialScope = CredentialScope.service
    model: ModelAlias | None = None


class Step(StepCore):
    """A playbook step: prompt is required."""

    prompt: Annotated[str, StringConstraints(min_length=1)]


class PlanStep(StepCore):
    """A mission plan step (prompt defaults to the title)."""

    status: StepStatus | None = None
    jobs: StepJobs | None = None

    @property
    def effective_prompt(self) -> str:
        return self.prompt or self.title or self.id


class StepJobs(ZModel):
    total: int = 0
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    needs_attention: int = 0


class PlaybookTrigger(ZModel):
    kind: TriggerKind
    cron: str | None = None
    timezone: str | None = None
    event: str | None = None
    filter: Json | None = None
    owner_user: str | None = None
    inputs: Json | None = None
    auto_start: bool = True

    @model_validator(mode="after")
    def _cron_for_schedule(self) -> PlaybookTrigger:
        if self.kind == TriggerKind.schedule and not self.cron:
            raise ValueError("schedule triggers need cron")
        return self


class PlaybookInputs(ZModel):
    schema_: Annotated[Json | None, Field(alias="schema")] = None


def check_step_order(steps: list[Any]) -> None:
    """Step ids are unique and `depends_on` names earlier steps only: the plan runs in order and has no cycle.
    (JSON Schema cannot say this; the scheduler relies on it.)"""
    seen: set[str] = set()
    for step in steps:
        if step.id in seen:
            raise ValueError(f"duplicate step id {step.id!r}")
        later = [d for d in step.depends_on if d not in seen]
        if later:
            raise ValueError(f"step {step.id!r} depends on {later}, which are not earlier steps")
        seen.add(step.id)


class Playbook(ZModel):
    """playbook.schema.json"""

    name: Slug
    version: SemVer
    domain: str | None = None
    description: Annotated[str, StringConstraints(min_length=1)]
    inputs: PlaybookInputs | None = None
    team: Annotated[list[Slug], Field(min_length=1)]
    connectors: list[Slug] = Field(default_factory=list)
    steps: Annotated[list[Step], Field(min_length=1)]
    trigger: PlaybookTrigger | None = None
    autonomy: Autonomy | None = None
    four_eyes: bool = False
    budget: MissionBudget | None = None
    data_class: DataClass | None = None
    baseline_minutes: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Playbook:
        check_step_order(self.steps)
        return self


class MissionPlan(ZModel):
    """mission-plan.schema.json"""

    goal: str | None = None
    playbook: str | None = None
    steps: Annotated[list[PlanStep], Field(min_length=1)]
    writes: list[WriteOperationId] = Field(default_factory=list)
    size: Literal["small", "medium", "large"] | None = None
    estimated_tokens: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def _ordered(self) -> MissionPlan:
        check_step_order(self.steps)
        return self


# ---------------------------------------------------------------- connector
class McpServer(ZModel):
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    env: dict[str, str] | None = None


class DeclaredOperation(ZModel):
    id: LocalOperationId
    description: str | None = None
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] | None = None
    path: str | None = None
    query: str | None = None
    command: list[str] | None = None
    flow: str | None = None
    input_schema: Json | None = None
    output_hint: str | None = None


BrowserAction = Literal[
    "goto",
    "click",
    "fill",
    "select",
    "check",
    "uncheck",
    "press",
    "wait_for",
    "wait_for_url",
    "extract_text",
    "extract_table",
    "extract_attribute",
    "assert_text",
    "assert_visible",
    "screenshot",
]


class BrowserStep(ZModel):
    action: BrowserAction
    selector: str | None = None
    url: str | None = None
    value: str | None = None
    key: str | None = None
    name: str | None = None
    attribute: str | None = None
    all: bool | None = None
    state: Literal["visible", "hidden", "attached", "detached"] | None = None
    full_page: bool | None = None
    optional: bool | None = None
    timeout_ms: Annotated[int, Field(ge=100, le=120_000)] | None = None


class BrowserFlow(ZModel):
    description: str | None = None
    start_url: str | None = None
    allowed_hosts: list[str] | None = None
    timeout_s: Annotated[int, Field(ge=5, le=300)] | None = None
    steps: Annotated[list[BrowserStep], Field(min_length=1, max_length=200)]

    @model_validator(mode="after")
    def _placeholders(self) -> BrowserFlow:
        from z_common.connectors.browser import check_flow

        check_flow(self.model_dump(exclude_none=True))
        return self


class SimpleAuth(ZModel):
    type: Literal["bearer", "api_key", "basic", "oauth", "mtls", "none"]
    secret: SecretRef | None = None
    header: str | None = None
    prefix: str | None = None
    username: str | None = None
    cert: SecretRef | None = None
    key: SecretRef | None = None


class OAuthFlow(ZModel):
    flow: Literal["authorization_code_pkce", "client_credentials"]
    app: Literal["read_app", "write_app"]
    scopes: list[str] | None = None


class ScopedAuth(ZModel):
    user: OAuthFlow | None = None
    service: OAuthFlow | None = None


class AuthSet(ZModel):
    read: SimpleAuth | ScopedAuth | None = None
    write: SimpleAuth | ScopedAuth | None = None


class OAuthApp(ZModel):
    client_id: str
    secret: SecretRef | None = None


class OAuthApps(ZModel):
    provider: Literal["microsoft", "google"] | None = None
    tenant_id: str | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    read_app: OAuthApp | None = None
    write_app: OAuthApp | None = None


class RateLimit(ZModel):
    per_minute: Annotated[int, Field(ge=1)] | None = None
    per_mission_per_minute: Annotated[int, Field(ge=1)] | None = None


class OperationRule(ZModel):
    match: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_*]+(\.[a-z0-9_*]+)*$")]
    class_: Annotated[OperationClass | None, Field(alias="class")] = None
    exposed: bool | None = None
    rate_limit: RateLimit | None = None
    args_map: Json | None = None


class OperationPolicy(ZModel):
    default_exposed: bool = False
    rules: list[OperationRule] = Field(default_factory=list)


class HealthProbe(ZModel):
    operation: LocalOperationId | None = None
    params: Json | None = None
    path: str | None = None
    interval_s: Annotated[int, Field(ge=10)] = 60


class WebhookConfig(ZModel):
    signature: Literal["z_hmac", "hub_signature", "gitlab_token"] | None = None
    secret: SecretRef | None = None


class ErrorBody(ZModel):
    """connector.schema.json `error_body`: a 2xx answer whose body field `path` equals `equals` is an error."""

    path: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")]
    equals: Any
    message: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")] | None = None


class CliSettings(ZModel):
    """connector.schema.json `cli` (ADR 0025)."""

    image: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$")]
    env: dict[str, str] | None = None
    timeout_s: Annotated[int, Field(ge=1, le=300)] | None = None
    output: Literal["text", "json"] | None = None


class ConnectorManifest(ZModel):
    """connector.schema.json"""

    name: Slug
    kind: ConnectorKind
    display_name: str | None = None
    description: str | None = None
    base_url: str | None = None
    spec: str | None = None
    operation_ids: Literal["from_operation_id", "from_path"] | None = None
    operation_map: dict[str, LocalOperationId] | None = None
    mcp: McpServer | None = None
    builtin: str | None = None
    config: Json | None = None
    declared: list[DeclaredOperation] | None = None
    flows: dict[str, BrowserFlow] | None = None
    auth: AuthSet | None = None
    oauth: OAuthApps | None = None
    operations: OperationPolicy | None = None
    rate_limit: RateLimit | None = None
    health: HealthProbe | None = None
    error_body: ErrorBody | None = None
    data_class: DataClass | None = None
    cli: CliSettings | None = None
    webhook: WebhookConfig | None = None


# ---------------------------------------------------------------- pack, skill
class PackManifest(ZModel):
    """pack.schema.json"""

    name: Slug
    version: SemVer
    description: Annotated[str, StringConstraints(min_length=1)]
    owner: str | None = None
    requires_connectors: list[Slug] = Field(default_factory=list)
    optional_connectors: list[Slug] = Field(default_factory=list)
    agents: list[Slug] | None = None
    skills: list[Slug] | None = None
    playbooks: list[Slug] | None = None
    connectors: list[Slug] | None = None


class SkillFrontMatter(ZModel):
    """skill-frontmatter.schema.json"""

    name: Slug
    description: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    license: str | None = None
    compatibility: Annotated[str, StringConstraints(max_length=500)] | None = None
    metadata: dict[str, str] | None = None
    allowed_tools: Annotated[str | None, Field(alias="allowed-tools")] = None
    version: SemVer | None = None
    evals: str | None = None
    owner: str | None = None


# ---------------------------------------------------------------- objective
class ObjectiveScope(ZModel):
    kind: AssignmentKind
    ref: str


class Metric(ZModel):
    source: Literal["z", "connector"]
    query: (
        Literal["untriaged_findings", "open_proposals", "stale_tickets", "overdue_tickets", "mission_failures"] | None
    ) = None
    operation: OperationId | None = None
    params: Json | None = None
    path: str | None = None
    aggregate: Literal["count", "sum"] = "count"
    target: float
    direction: Literal["down", "up"]

    @model_validator(mode="after")
    def _source_fields(self) -> Metric:
        if self.source == "z" and self.query is None:
            raise ValueError("z metrics need query")
        if self.source == "connector" and self.operation is None:
            raise ValueError("connector metrics need operation")
        return self


class EventCadence(ZModel):
    event: TriggerKind


class BudgetPerPeriod(ZModel):
    tokens: Annotated[int, Field(ge=1)]
    period: Literal["day", "week", "month"]


class ObjectiveManifest(ZModel):
    """objective.schema.json"""

    agent: Slug
    title: Annotated[str, StringConstraints(min_length=1)]
    scope: list[ObjectiveScope] = Field(default_factory=list)
    metric: Metric
    cadence: str | EventCadence
    allowed_playbooks: list[Slug] = Field(default_factory=list)
    mode: ObjectiveMode = ObjectiveMode.suggest
    budget_per_period: BudgetPerPeriod | None = None
    owner_user: str | None = None
    status: ObjectiveStatus | None = None


# ---------------------------------------------------------------- eval case
CheckType = Literal[
    "json_schema",
    "field_equals",
    "contains",
    "regex",
    "artifact_exists",
    "diff_contains",
    "tests_pass",
    "asked_human",
    "count_actions",
    "no_action",
    "no_write_tools",
    "has_sources",
    "duplicate_detected",
]
CHECK_PARAMS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # type: (required params, optional params)
    "json_schema": (frozenset({"schema"}), frozenset({"path"})),
    "field_equals": (frozenset({"path", "value"}), frozenset()),
    "contains": (frozenset({"path", "value"}), frozenset()),
    "regex": (frozenset({"path", "pattern"}), frozenset()),
    "artifact_exists": (frozenset({"name"}), frozenset()),
    "diff_contains": (frozenset({"value"}), frozenset()),
    "tests_pass": (frozenset(), frozenset()),
    "asked_human": (frozenset(), frozenset()),
    "count_actions": (frozenset({"operation", "equals"}), frozenset()),
    "no_action": (frozenset({"operation"}), frozenset()),
    "no_write_tools": (frozenset(), frozenset()),
    "has_sources": (frozenset({"min"}), frozenset()),
    "duplicate_detected": (frozenset(), frozenset()),
}


class Check(ZModel):
    model_config = ZModel.model_config | {"extra": "allow"}

    type: CheckType

    @model_validator(mode="after")
    def _params(self) -> Check:
        required, optional = CHECK_PARAMS[self.type]
        given = set((self.model_extra or {}).keys())
        missing = required - given
        unknown = given - required - optional
        if missing or unknown:
            raise ValueError(f"check {self.type}: missing {sorted(missing)} unknown {sorted(unknown)}")
        return self

    def param(self, name: str) -> Any:
        return (self.model_extra or {})[name]


class EvalCase(ZModel):
    """eval-case.schema.json"""

    id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
    description: str | None = None
    input: Json
    context_fixtures: dict[str, str | Json | list[Any]] | None = None
    expected: Json | None = None
    rubric: list[Annotated[str, StringConstraints(min_length=1)]] = Field(default_factory=list)
    checks: list[Check]
    tags: list[str] | None = None
    skill: Slug | None = None
    timeout_s: Annotated[int, Field(ge=1)] | None = None


PlanStep.model_rebuild()
