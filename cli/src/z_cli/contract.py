"""The z-common models (generated from contracts/) on both sides of every call the CLI makes.

Request bodies are validated strictly before they are sent, so the CLI never sends a field, enum value or
shape the contract does not have (that would be a CLI bug, reported as a usage error). Responses are
validated against the operation's model: unknown extra keys are tolerated (a newer server may add fields),
a missing or mistyped field is a contract mismatch.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import ValidationError
from z_common.models import api_catalog as cat
from z_common.models import api_identity as ident
from z_common.models import api_missions as mis
from z_common.models.base import ZModel
from z_common.models.events import Event

from z_cli.errors import ContractMismatch, UsageError

Shape = Literal["one", "list", "page"]
ID = r"[^/]+"


def _ops() -> list[tuple[str, re.Pattern[str], type[ZModel] | None, type[ZModel] | None, Shape]]:
    table: list[tuple[str, str, type[ZModel] | None, type[ZModel] | None, Shape]] = [
        ("GET", "/auth/cli/config", None, ident.CliAuthConfig, "one"),
        ("POST", "/auth/cli/exchange", ident.CliExchangeRequest, ident.CliToken, "one"),
        ("GET", "/me", None, ident.Me, "one"),
        ("GET", "/me/sessions", None, ident.AuthSession, "list"),
        ("GET", "/me/tokens", None, ident.ApiToken, "list"),
        ("POST", "/me/tokens", ident.ApiTokenCreate, ident.ApiToken, "one"),
        ("POST", "/missions", mis.MissionCreate, mis.Mission, "one"),
        ("GET", "/missions", None, mis.Mission, "page"),
        ("GET", f"/missions/{ID}", None, mis.Mission, "one"),
        ("POST", f"/missions/{ID}/(start|pause|resume|cancel)", mis.MissionControl, mis.Mission, "one"),
        ("POST", f"/missions/{ID}/messages", mis.GuideMessage, None, "one"),
        ("POST", f"/missions/{ID}/files", None, mis.Artifact, "one"),
        ("POST", f"/missions/{ID}/take-over", mis.TakeOver, mis.TakeOverSession, "one"),
        ("GET", f"/missions/{ID}/jobs", None, mis.Job, "page"),
        ("GET", f"/missions/{ID}/replay", None, mis.Replay, "one"),
        ("GET", f"/jobs/{ID}", None, mis.Job, "one"),
        ("POST", f"/jobs/{ID}/retry", mis.MissionControl, mis.Job, "one"),
        ("GET", f"/jobs/{ID}/artifacts", None, mis.Artifact, "list"),
        ("GET", "/review", None, mis.ReviewList, "one"),
        ("GET", f"/actions/{ID}", None, mis.ProposedAction, "one"),
        ("POST", f"/actions/{ID}/approve", mis.ApproveRequest, mis.ProposedAction, "one"),
        ("POST", f"/actions/{ID}/reject", mis.RejectRequest, mis.ProposedAction, "one"),
        ("POST", f"/actions/{ID}/edit", mis.EditRequest, mis.ProposedAction, "one"),
        ("POST", "/actions:batch-approve", mis.BatchApprove, mis.BatchResult, "one"),
        ("POST", f"/questions/{ID}/answer", mis.AnswerRequest, mis.Question, "one"),
        ("POST", f"/playbooks/{ID}/run", mis.PlaybookRun, mis.Mission, "one"),
        ("GET", "/agents", None, cat.Agent, "page"),
        ("GET", f"/agents/{ID}", None, cat.Agent, "one"),
        ("GET", f"/agents/{ID}/versions", None, cat.AgentVersion, "list"),
        ("GET", "/skills", None, cat.Skill, "page"),
        ("GET", f"/skills/{ID}", None, cat.Skill, "one"),
        ("GET", f"/skills/{ID}/versions", None, cat.SkillVersion, "list"),
        ("POST", f"/skills/{ID}/rollback", cat.SkillRollback, cat.Skill, "one"),
        ("GET", "/playbooks", None, cat.Playbook, "page"),
        ("GET", f"/playbooks/{ID}", None, cat.Playbook, "one"),
        ("GET", "/connectors", None, cat.Connector, "list"),
        ("GET", f"/connectors/{ID}", None, cat.Connector, "one"),
        ("GET", f"/connectors/{ID}/operations", None, cat.Operation, "list"),
        ("POST", f"/connectors/{ID}/approve-exposure", cat.ApproveExposure, cat.Connector, "one"),
        ("PATCH", f"/operations/{ID}", cat.OperationPatch, cat.Operation, "one"),
        ("GET", "/audit", None, ident.AuditEvent, "page"),
        ("POST", "/admin/audit:verify", None, ident.AuditVerify, "one"),
        ("GET", "/admin/users", None, ident.User, "page"),
        ("GET", f"/admin/users/{ID}", None, ident.UserDetail, "one"),
        ("PATCH", f"/admin/users/{ID}", ident.UserPatch, ident.User, "one"),
        ("GET", "/admin/role-bindings", None, ident.RoleBinding, "list"),
        ("POST", "/admin/role-bindings", ident.RoleBindingWrite, ident.RoleBinding, "one"),
        ("GET", "/admin/roles", None, ident.Role, "list"),
        ("POST", "/admin/roles", ident.RoleWrite, ident.Role, "one"),
        ("GET", "/admin/permissions", None, ident.PermissionInfo, "list"),
        ("GET", "/admin/who-can", None, ident.WhoCan, "one"),
        ("GET", "/admin/group-mapping", None, ident.GroupMappingConfig, "one"),
        ("PUT", "/admin/group-mapping", ident.GroupMappingConfig, ident.GroupMappingConfig, "one"),
        ("POST", "/admin/group-mapping:dry-run", ident.GroupMappingConfig, ident.GroupMappingDryRun, "one"),
        ("GET", "/admin/sessions", None, ident.AuthSession, "page"),
        ("GET", "/admin/tokens", None, ident.ApiToken, "page"),
        ("GET", "/teams", None, ident.Team, "list"),
        ("GET", f"/teams/{ID}", None, ident.Team, "one"),
        ("POST", "/teams", ident.TeamWrite, ident.Team, "one"),
        ("PATCH", f"/teams/{ID}", ident.TeamWrite, ident.Team, "one"),
    ]
    return [(method, re.compile(f"^{path}$"), req, resp, shape) for method, path, req, resp, shape in table]


OPERATIONS = _ops()


def lookup(method: str, path: str) -> tuple[type[ZModel] | None, type[ZModel] | None, Shape] | None:
    path = path.split("?", 1)[0]
    for m, pattern, req, resp, shape in OPERATIONS:
        if m == method.upper() and pattern.match(path):
            return req, resp, shape
    return None


def _real(exc: ValidationError, *, strict: bool) -> list[str]:
    errors = [e for e in exc.errors() if strict or e["type"] != "extra_forbidden"]
    return [f"{'.'.join(str(p) for p in e['loc']) or '(body)'}: {e['msg']}" for e in errors[:5]]


def check_request(method: str, path: str, body: Any) -> None:
    found = lookup(method, path)
    if found is None or found[0] is None or body is None:
        return
    try:
        found[0].model_validate(body)
    except ValidationError as exc:
        raise UsageError("invalid request: " + "; ".join(_real(exc, strict=True))) from exc


def check(model: type[ZModel], data: Any, what: str) -> Any:
    """Returns `data` unchanged when it matches `model` (extra keys allowed)."""
    try:
        model.model_validate(data)
    except ValidationError as exc:
        problems = _real(exc, strict=False)
        if problems:
            raise ContractMismatch(what, "; ".join(problems)) from exc
    return data


def check_response(method: str, path: str, data: Any) -> Any:
    found = lookup(method, path)
    if found is None or found[1] is None:
        return data
    _, model, shape = found
    assert model is not None
    what = f"response to {method.upper()} {path.split('?', 1)[0]}"
    if shape == "one":
        return check(model, data, what)
    items = data.get("items") if shape == "page" and isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ContractMismatch(what, "expected a list" if shape == "list" else "expected {items, next_cursor}")
    for item in items:
        check(model, item, what)
    return data


def check_event(data: Any) -> dict[str, Any]:
    """An SSE event envelope (contracts/events.schema.json)."""
    checked: dict[str, Any] = check(Event, data, "event stream")
    return checked
