"""Every hand-written Pydantic model has exactly the properties and required keys of its contract schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from z_common import contracts
from z_common.contracts import load_document
from z_common.models import (
    actions,
    api_catalog,
    api_identity,
    api_internal,
    api_missions,
    api_ops,
    common,
    events,
    manifests,
)
from z_common.models.enums import ENUM_BY_SCHEMA_DEF


def _deref(ref: str, base: str) -> tuple[Any, str]:
    file, _, pointer = ref.partition("#")
    file = file or base
    doc: Any = contracts.openapi() if file == "openapi.yaml" else contracts.load_schema(file)
    for part in [p for p in pointer.split("/") if p]:
        doc = doc[part]
    return doc, file


def effective(schema: Any, base: str) -> tuple[set[str], set[str]]:
    """(properties, required) after following $ref and allOf (if/then branches are ignored)."""
    props: set[str] = set()
    required: set[str] = set()
    if "$ref" in schema:
        target, file = _deref(schema["$ref"], base)
        p, r = effective(target, file)
        props |= p
        required |= r
    for sub in schema.get("allOf", []):
        p, r = effective(sub, base)
        props |= p
        required |= r
    props |= set(schema.get("properties", {}))
    required |= set(schema.get("required", []))
    return props, required


def model_keys(model: type[BaseModel]) -> tuple[set[str], set[str]]:
    props, required = set(), set()
    for name, field in model.model_fields.items():
        key = field.alias or name
        props.add(key)
        if field.is_required():
            required.add(key)
    return props, required


CASES: list[tuple[str, str, type[BaseModel]]] = [
    # (file, pointer, model)
    ("agent.schema.json", "", manifests.AgentProfile),
    ("playbook.schema.json", "", manifests.Playbook),
    ("playbook.schema.json", "/$defs/Step", manifests.Step),
    ("playbook.schema.json", "/$defs/Fanout", manifests.Fanout),
    ("playbook.schema.json", "/$defs/StepOutput", manifests.StepOutput),
    ("playbook.schema.json", "/$defs/PlaybookTrigger", manifests.PlaybookTrigger),
    ("mission-plan.schema.json", "", manifests.MissionPlan),
    ("mission-plan.schema.json", "/$defs/PlanStep", manifests.PlanStep),
    ("connector.schema.json", "", manifests.ConnectorManifest),
    ("connector.schema.json", "/$defs/McpServer", manifests.McpServer),
    ("connector.schema.json", "/$defs/DeclaredOperation", manifests.DeclaredOperation),
    ("connector.schema.json", "/$defs/SimpleAuth", manifests.SimpleAuth),
    ("connector.schema.json", "/$defs/OAuthFlow", manifests.OAuthFlow),
    ("connector.schema.json", "/$defs/ScopedAuth", manifests.ScopedAuth),
    ("connector.schema.json", "/$defs/AuthSet", manifests.AuthSet),
    ("connector.schema.json", "/$defs/OAuthApp", manifests.OAuthApp),
    ("connector.schema.json", "/$defs/OAuthApps", manifests.OAuthApps),
    ("connector.schema.json", "/$defs/OperationRule", manifests.OperationRule),
    ("connector.schema.json", "/$defs/OperationPolicy", manifests.OperationPolicy),
    ("connector.schema.json", "/$defs/RateLimit", manifests.RateLimit),
    ("connector.schema.json", "/$defs/HealthProbe", manifests.HealthProbe),
    ("pack.schema.json", "", manifests.PackManifest),
    ("skill-frontmatter.schema.json", "", manifests.SkillFrontMatter),
    ("objective.schema.json", "", manifests.ObjectiveManifest),
    ("objective.schema.json", "/$defs/Metric", manifests.Metric),
    ("eval-case.schema.json", "", manifests.EvalCase),
    ("action.schema.json", "", actions.ActionEnvelope),
    ("action.schema.json", "/$defs/GitlabMergeRequestCreate", actions.GitlabMergeRequestCreate),
    ("action.schema.json", "/$defs/GitlabIssueCreate", actions.GitlabIssueCreate),
    ("action.schema.json", "/$defs/GitlabNoteCreate", actions.GitlabNoteCreate),
    ("action.schema.json", "/$defs/JiraIssueCreate", actions.JiraIssueCreate),
    ("action.schema.json", "/$defs/JiraIssueUpdate", actions.JiraIssueUpdate),
    ("action.schema.json", "/$defs/JiraCommentCreate", actions.JiraCommentCreate),
    ("action.schema.json", "/$defs/DiffPreview", actions.DiffPreview),
    ("action.schema.json", "/$defs/IssuePreview", actions.IssuePreview),
    ("action.schema.json", "/$defs/IssueUpdatePreview", actions.IssueUpdatePreview),
    ("action.schema.json", "/$defs/CommentPreview", actions.CommentPreview),
    ("action.schema.json", "/$defs/MailPreview", actions.MailPreview),
    ("action.schema.json", "/$defs/EventPreview", actions.EventPreview),
    ("action.schema.json", "/$defs/ChatMessagePreview", actions.ChatMessagePreview),
    ("action.schema.json", "/$defs/FilePreview", actions.FilePreview),
    ("action.schema.json", "/$defs/JsonPreview", actions.JsonPreview),
    ("events.schema.json", "", events.Event),
    ("events.schema.json", "/$defs/EventIn", events.EventIn),
    ("common.schema.json", "/$defs/Avatar", common.Avatar),
    ("common.schema.json", "/$defs/Persona", common.Persona),
    ("common.schema.json", "/$defs/MemoryConfig", common.MemoryConfig),
    ("common.schema.json", "/$defs/Limits", common.Limits),
    ("common.schema.json", "/$defs/MissionBudget", common.MissionBudget),
    ("common.schema.json", "/$defs/RunsAs", common.RunsAs),
    ("common.schema.json", "/$defs/Problem", common.Problem),
    ("common.schema.json", "/$defs/FieldError", common.FieldError),
]
CASES += [("events.schema.json", f"/$defs/{model.__name__}", model) for model in events.PAYLOADS.values()]

# openapi components implemented so far; the list grows with each build step (see test_api_coverage)
API_MODELS: dict[str, type[BaseModel]] = {
    name: getattr(api_identity, name)
    for name in [
        "EffectiveRole",
        "TeamRef",
        "User",
        "Me",
        "Team",
        "TeamWrite",
        "Role",
        "RoleWrite",
        "PermissionInfo",
        "RoleBinding",
        "RoleBindingWrite",
        "GroupMapping",
        "GroupMappingConfig",
        "AccessChange",
        "GroupMappingDryRun",
        "WhoCan",
        "AuthSession",
        "ApiToken",
        "ApiTokenCreate",
        "AuthSettings",
        "AuthSettingsTest",
        "CliAuthConfig",
        "CliExchangeRequest",
        "CliToken",
        "LocalLoginRequest",
        "StepUpResponse",
        "LoginRedirect",
        "UserPatch",
        "AuditEvent",
        "AuditCheckpoint",
        "AuditVerify",
        "AuditSettings",
        "Guardrail",
        "Health",
        "UserDetail",
    ]
}
API_MODELS["PersonalConnection"] = api_identity.PersonalConnectionInfo
for _name in [
    "Agent",
    "AgentPatch",
    "AgentDuplicate",
    "AgentVersion",
    "Skill",
    "SkillVersion",
    "SkillRollback",
    "Playbook",
    "Connector",
    "ConnectorHealthInfo",
    "CredentialInfo",
    "Operation",
    "OperationPatch",
    "ApproveExposure",
    "ConnectorPatch",
    "ConnectorTestRequest",
    "ConnectorTestResult",
    "ConnectorImport",
    "ConnectorDraft",
]:
    API_MODELS[_name] = getattr(api_catalog, _name)
for _name in [
    "Task",
    "TaskPatch",
    "Trigger",
    "TriggerCreate",
    "WebhookAccepted",
    "Session",
    "SessionCreate",
    "SessionMessage",
    "SessionDetail",
    "SessionMessageIn",
    "SessionReply",
    "InferenceModel",
    "InferenceNode",
    "InferenceHealth",
    "UsageBucket",
    "UsageSeries",
    "Usage",
    "Budget",
    "BudgetWrite",
    "Provider",
    "ProviderWrite",
    "Analytics",
    "Objective",
    "ObjectivePatch",
    "ObjectiveCheck",
    "Assignment",
    "AssignmentCreate",
    "ProposalDecision",
]:
    API_MODELS[_name] = getattr(api_ops, _name)
for _name in [
    "SkillBundle",
    "MemorySnippet",
    "GatewayTool",
    "WriteTarget",
    "JobSpec",
    "EventBatch",
    "JobControl",
    "CheckpointIn",
    "ActionIn",
    "QuestionIn",
    "MemorySearch",
    "SubagentIn",
    "SubagentStatus",
    "ObjectiveReport",
    "JobComplete",
    "WorkspaceManifest",
    "DataClassRaise",
]:
    API_MODELS[_name] = getattr(api_internal, _name)
for _name in [
    "JobCounts",
    "Mission",
    "MissionCreate",
    "DraftPlanRequest",
    "DraftPlan",
    "PlanPatch",
    "MissionControl",
    "GuideMessage",
    "TakeOver",
    "TakeOverSession",
    "SaveAsPlaybook",
    "PlaybookRun",
    "Job",
    "Artifact",
    "Replay",
    "Approval",
    "JudgeCheck",
    "ProposedAction",
    "Question",
    "MissionSuggestion",
    "ReviewItem",
    "ReviewList",
    "ApproveRequest",
    "RejectRequest",
    "EditRequest",
    "BatchApprove",
    "BatchResult",
    "AnswerRequest",
]:
    API_MODELS[_name] = getattr(api_missions, _name)


@pytest.mark.parametrize(("file", "pointer", "model"), CASES, ids=[f"{f}{p}" for f, p, _ in CASES])
def test_manifest_models_match_schema(file: str, pointer: str, model: type[BaseModel]) -> None:
    schema, base = _deref("#" + pointer, file) if pointer else (contracts.load_schema(file), file)
    assert model_keys(model) == effective(schema, base)


@pytest.mark.parametrize("name", sorted(API_MODELS))
def test_api_models_match_openapi(name: str) -> None:
    schema = contracts.openapi()["components"]["schemas"][name]
    assert model_keys(API_MODELS[name]) == effective(schema, "openapi.yaml")


def test_enums_match_common_schema() -> None:
    defs = contracts.load_schema("common.schema.json")["$defs"]
    for def_name, enum in ENUM_BY_SCHEMA_DEF.items():
        assert {e.value for e in enum} == set(defs[def_name]["enum"]), def_name
    schema_enums = {name for name, d in defs.items() if "enum" in d}
    assert schema_enums == set(ENUM_BY_SCHEMA_DEF)


EXAMPLE_MODELS: dict[str, type[BaseModel]] = {
    "agent": manifests.AgentProfile,
    "playbook": manifests.Playbook,
    "connector": manifests.ConnectorManifest,
    "pack": manifests.PackManifest,
    "skill-frontmatter": manifests.SkillFrontMatter,
    "objective": manifests.ObjectiveManifest,
    "eval-case": manifests.EvalCase,
    "mission-plan": manifests.MissionPlan,
    "action": actions.ActionEnvelope,
    "events": events.Event,
}


def _examples() -> list[tuple[str, Path]]:
    root = contracts.contracts_dir() / "examples"
    return [
        (folder.name, p)
        for folder in sorted(root.iterdir())
        if folder.name in EXAMPLE_MODELS
        for p in sorted(folder.iterdir())
    ]


@pytest.mark.parametrize(("kind", "path"), _examples(), ids=lambda v: getattr(v, "name", v))
def test_models_agree_with_schema_on_examples(kind: str, path: Path) -> None:
    model = EXAMPLE_MODELS[kind]
    document = load_document(path)
    if path.name.startswith("invalid-"):
        with pytest.raises(ValidationError):
            parsed = model.model_validate(document)
            if isinstance(parsed, events.Event):
                parsed.typed_payload()
    else:
        parsed = model.model_validate(document)
        if isinstance(parsed, events.Event):
            parsed.typed_payload()


def test_every_seed_manifest_parses_with_models(repo: Path) -> None:
    import yaml

    kinds = {
        "agents": manifests.AgentProfile,
        "playbooks": manifests.Playbook,
        "connectors": manifests.ConnectorManifest,
    }
    for folder, model in kinds.items():
        for path in sorted(repo.glob(f"packs/*/{folder}/*.yaml")):
            model.model_validate(yaml.safe_load(path.read_text()))
