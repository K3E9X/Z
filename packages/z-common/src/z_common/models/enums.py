"""Enums of contracts/common.schema.json. A test asserts they match the schema exactly."""

from __future__ import annotations

from enum import StrEnum


class DataClass(StrEnum):
    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"

    @property
    def rank(self) -> int:
        return int(self.value[1])

    def raised_to(self, other: DataClass) -> DataClass:
        """The class only goes up."""
        return other if other.rank > self.rank else self


class Autonomy(StrEnum):
    read_only = "read_only"
    propose = "propose"

    @staticmethod
    def strictest(*values: Autonomy | None) -> Autonomy:
        present = [v for v in values if v is not None]
        return Autonomy.read_only if Autonomy.read_only in present else Autonomy.propose


class OperationClass(StrEnum):
    read = "read"
    write = "write"
    destructive = "destructive"


class MissionStatus(StrEnum):
    draft = "draft"
    planned = "planned"
    queued = "queued"
    running = "running"
    waiting = "waiting"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    budget_exceeded = "budget_exceeded"
    timed_out = "timed_out"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_MISSION


TERMINAL_MISSION = frozenset(
    {
        MissionStatus.succeeded,
        MissionStatus.failed,
        MissionStatus.cancelled,
        MissionStatus.budget_exceeded,
        MissionStatus.timed_out,
    }
)


class WaitingReason(StrEnum):
    approval = "approval"
    question = "question"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    needs_attention = "needs_attention"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    budget_exceeded = "budget_exceeded"
    timed_out = "timed_out"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_JOB


TERMINAL_JOB = frozenset(
    {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled, JobStatus.budget_exceeded, JobStatus.timed_out}
)


class NeedsAttentionReason(StrEnum):
    question = "question"
    approval = "approval"
    invalid_output = "invalid_output"
    loop_detected = "loop_detected"
    guardrail_confirm = "guardrail_confirm"
    connector_down = "connector_down"
    judge_blocked = "judge_blocked"


class StepStatus(StrEnum):
    pending = "pending"
    running = "running"
    waiting = "waiting"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"
    cancelled = "cancelled"


class ActionStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    applying = "applying"
    applied = "applied"
    failed = "failed"
    rejected = "rejected"
    superseded = "superseded"


class ProposalStatus(StrEnum):
    observed = "observed"
    proposed = "proposed"
    evaluating = "evaluating"
    ready = "ready"
    discarded = "discarded"
    applied = "applied"
    rejected = "rejected"
    rolled_back = "rolled_back"


class ProposalKind(StrEnum):
    skill_edit = "skill_edit"
    memory_fact = "memory_fact"
    routing_rule = "routing_rule"
    playbook_edit = "playbook_edit"


class SignalKind(StrEnum):
    action_edited = "action_edited"
    action_rejected = "action_rejected"
    question_answered = "question_answered"
    mission_reassigned = "mission_reassigned"
    retry = "retry"
    repair = "repair"
    failure = "failure"
    guardrail_hit = "guardrail_hit"
    feedback = "feedback"
    connector_error = "connector_error"


class ConnectorHealth(StrEnum):
    healthy = "healthy"
    degraded = "degraded"
    token_expiring = "token_expiring"  # noqa: S105 (status name, not a secret)
    unreachable = "unreachable"
    disabled = "disabled"


class ConnectorKind(StrEnum):
    mcp = "mcp"
    openapi = "openapi"
    rest = "rest"
    graphql = "graphql"
    cli = "cli"
    database = "database"
    browser = "browser"
    webhook = "webhook"
    builtin = "builtin"


class ReviewStatus(StrEnum):
    pending_review = "pending_review"
    approved = "approved"


class ObjectiveStatus(StrEnum):
    on_track = "on_track"
    at_risk = "at_risk"
    off_track = "off_track"
    paused = "paused"


class ObjectiveMode(StrEnum):
    suggest = "suggest"
    auto_start_read_only = "auto_start_read_only"


class TaskSource(StrEnum):
    jira = "jira"
    gitlab = "gitlab"
    webhook = "webhook"
    email = "email"
    chat = "chat"
    schedule = "schedule"
    manual = "manual"


class TaskStatus(StrEnum):
    incoming = "incoming"
    scheduled = "scheduled"
    started = "started"
    dismissed = "dismissed"


class TriggerKind(StrEnum):
    schedule = "schedule"
    jira = "jira"
    gitlab = "gitlab"
    webhook = "webhook"
    email = "email"
    chat = "chat"


class Surface(StrEnum):
    cli = "cli"
    web = "web"
    teams = "teams"
    slack = "slack"
    mattermost = "mattermost"


class ChannelKind(StrEnum):
    teams = "teams"
    slack = "slack"
    mattermost = "mattermost"
    email = "email"


class AssignmentKind(StrEnum):
    repo = "repo"
    group = "group"
    jira_project = "jira_project"
    product = "product"
    mailbox = "mailbox"
    channel = "channel"
    connector = "connector"
    document_space = "document_space"
    custom = "custom"


class UserStatus(StrEnum):
    active = "active"
    suspended = "suspended"
    deprovisioned = "deprovisioned"


class SessionKind(StrEnum):
    web = "web"
    cli = "cli"


class BindingSource(StrEnum):
    gitlab = "gitlab"
    manual = "manual"


class MappingSource(StrEnum):
    config = "config"
    db = "db"


class CredentialPurpose(StrEnum):
    read = "read"
    write = "write"


class CredentialScope(StrEnum):
    service = "service"
    user = "user"


class JudgeVerdict(StrEnum):
    clean = "clean"
    flagged = "flagged"
    blocked = "blocked"


class WorkspaceMode(StrEnum):
    none = "none"
    git_clone = "git_clone"
    files = "files"


class BudgetScopeKind(StrEnum):
    org = "org"
    team = "team"
    user = "user"
    playbook = "playbook"
    mission = "mission"
    agent = "agent"
    objective = "objective"


class BudgetPeriod(StrEnum):
    mission = "mission"
    day = "day"
    week = "week"
    month = "month"


class ArtifactKind(StrEnum):
    result = "result"
    report = "report"
    document = "document"
    patch = "patch"
    diff = "diff"
    terminal = "terminal"
    screenshot = "screenshot"
    data = "data"
    image = "image"
    log = "log"
    archive = "archive"
    other = "other"


class BuiltinRole(StrEnum):
    viewer = "viewer"
    operator = "operator"
    approver = "approver"
    builder = "builder"
    auditor = "auditor"
    admin = "admin"


ENUM_BY_SCHEMA_DEF: dict[str, type[StrEnum]] = {
    "DataClass": DataClass,
    "Autonomy": Autonomy,
    "OperationClass": OperationClass,
    "MissionStatus": MissionStatus,
    "WaitingReason": WaitingReason,
    "JobStatus": JobStatus,
    "NeedsAttentionReason": NeedsAttentionReason,
    "StepStatus": StepStatus,
    "ActionStatus": ActionStatus,
    "ProposalStatus": ProposalStatus,
    "ProposalKind": ProposalKind,
    "SignalKind": SignalKind,
    "ConnectorHealth": ConnectorHealth,
    "ConnectorKind": ConnectorKind,
    "ReviewStatus": ReviewStatus,
    "ObjectiveStatus": ObjectiveStatus,
    "ObjectiveMode": ObjectiveMode,
    "TaskSource": TaskSource,
    "TaskStatus": TaskStatus,
    "TriggerKind": TriggerKind,
    "Surface": Surface,
    "ChannelKind": ChannelKind,
    "AssignmentKind": AssignmentKind,
    "UserStatus": UserStatus,
    "SessionKind": SessionKind,
    "BindingSource": BindingSource,
    "MappingSource": MappingSource,
    "CredentialPurpose": CredentialPurpose,
    "CredentialScope": CredentialScope,
    "JudgeVerdict": JudgeVerdict,
    "WorkspaceMode": WorkspaceMode,
    "BudgetScopeKind": BudgetScopeKind,
    "BudgetPeriod": BudgetPeriod,
    "ArtifactKind": ArtifactKind,
    "BuiltinRole": BuiltinRole,
}
