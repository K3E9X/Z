"""Pack loading, lint rules, operation classifier and OpenAPI operation ids."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from z_common.config import substitute
from z_common.connectors.openapi import id_from_path, operations_from_spec, slug
from z_common.models.enums import OperationClass
from z_common.packs import discover
from z_common.packs.classify import classify, glob_match, raise_only
from z_common.packs.lint import lint, main


def test_seed_packs_lint_clean(repo: Path) -> None:
    assert main([str(repo / "packs")]) == 0


@pytest.mark.parametrize(
    ("local_id", "method", "hint", "expected"),
    [
        ("findings.list", "GET", None, "read"),
        ("findings.create", "POST", None, "write"),
        ("accounts.disable", "POST", None, "destructive"),
        ("accounts.delete", "DELETE", None, "destructive"),
        ("branches.remove_protection", "PATCH", None, "destructive"),
        ("merge_requests.create", None, None, "write"),
        ("merge_requests.merge", None, None, "destructive"),
        ("merge_requests.diff", None, None, "read"),
        ("issues.search", None, None, "read"),
        ("issues.search", None, False, "write"),
        ("pages.get", None, True, "read"),
        ("mail.send", None, True, "write"),
        ("deployments.approve", "GET", None, "destructive"),
    ],
)
def test_classifier(local_id: str, method: str | None, hint: bool | None, expected: str) -> None:
    assert classify(local_id, method, hint) == OperationClass(expected)


def test_runtime_classifier_requires_mcp_read_hint() -> None:
    assert classify("issues.search", None, None, require_hint=True) == OperationClass.write
    assert classify("issues.search", None, True, require_hint=True) == OperationClass.read


def test_rules_only_raise() -> None:
    assert raise_only(OperationClass.destructive, OperationClass.write) == OperationClass.destructive
    assert raise_only(OperationClass.read, OperationClass.write) == OperationClass.write
    assert raise_only(OperationClass.write, None) == OperationClass.write


@pytest.mark.parametrize(
    ("pattern", "value", "expected"),
    [
        ("defectdojo.findings.*", "defectdojo.findings.list", True),
        ("defectdojo.findings.*", "defectdojo.products.list", False),
        ("confluence.pages.*", "confluence.pages.search", True),
        ("m365.*", "m365.calendar.events.list", True),
        ("web.fetch", "web.fetch", True),
        ("web.*.get", "web.fetch", False),
    ],
)
def test_glob(pattern: str, value: str, expected: bool) -> None:
    assert glob_match(pattern, value) is expected


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/v2/findings/", "findings.list"),
        ("GET", "/api/v2/findings/{id}/", "findings.get"),
        ("POST", "/api/v2/findings/", "findings.create"),
        ("PUT", "/api/v2/findings/{id}/", "findings.update"),
        ("PATCH", "/api/v2/findings/{id}/", "findings.update"),
        ("DELETE", "/api/v2/findings/{id}/", "findings.delete"),
        ("POST", "/accounts/{id}/disable", "accounts.disable"),
        ("GET", "/api/v2/engagements/{id}/", "engagements.get"),
    ],
)
def test_ids_from_path(method: str, path: str, expected: str) -> None:
    assert id_from_path(method, path) == expected


def test_ids_from_operation_id(repo: Path) -> None:
    ops = operations_from_spec(repo / "packs/ticketing/connectors/logos.openapi.json", "from_operation_id")
    assert ops == {"assets.get": "GET", "accounts.list": "GET", "accounts.disable": "POST", "accounts.delete": "DELETE"}
    assert slug("listFindings") == "list_findings"


def test_substitution_types_and_defaults() -> None:
    env = {"N": "", "S": "a:b # c", "B": "true"}
    assert substitute("${N:-1024}", env) == 1024
    assert substitute("${S}", env) == "a:b # c"
    assert substitute("${B}", env) is True
    assert substitute("x-${MISSING:-y}", env) == "x-y"
    assert substitute(["${MISSING}"], env) == [""]


# ---------------------------------------------------------------- negative lint cases
@pytest.fixture
def packs_copy(repo: Path, tmp_path: Path) -> Path:
    target = tmp_path / "packs"
    shutil.copytree(repo / "packs", target)
    return target


def _edit(path: Path, change: dict[str, object] | None = None, **kwargs: object) -> None:
    data = yaml.safe_load(path.read_text())
    for key, value in {**(change or {}), **kwargs}.items():
        data[key] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _errors(packs_root: Path) -> list[str]:
    return lint(discover(packs_root), packs_root.parent).errors


def test_lint_write_in_agent_tools(packs_copy: Path) -> None:
    path = packs_copy / "ticketing/agents/ticket-manager.yaml"
    data = yaml.safe_load(path.read_text())
    data["tools"].append("gateway:jira.issues.create")
    path.write_text(yaml.safe_dump(data))
    assert any("writes are proposed, never listed as tools" in e for e in _errors(packs_copy))


def test_lint_step_agent_not_in_team(packs_copy: Path) -> None:
    path = packs_copy / "vuln/playbooks/weekly-vuln-triage.yaml"
    data = yaml.safe_load(path.read_text())
    data["team"] = ["vuln-analyst"]
    path.write_text(yaml.safe_dump(data))
    assert any("is not in the team" in e for e in _errors(packs_copy))


def test_lint_missing_skill_and_eval(packs_copy: Path) -> None:
    path = packs_copy / "vuln/agents/vuln-analyst.yaml"
    _edit(path, skills=["triage-finding", "nope"], evals="evals/missing.jsonl")
    errors = _errors(packs_copy)
    assert any("skill 'nope' does not exist" in e for e in errors)
    assert any("eval file evals/missing.jsonl not found" in e for e in errors)


def test_lint_fanout_source_needs_output_schema(packs_copy: Path) -> None:
    path = packs_copy / "vuln/playbooks/weekly-vuln-triage.yaml"
    data = yaml.safe_load(path.read_text())
    del data["steps"][0]["output"]
    path.write_text(yaml.safe_dump(data))
    assert any("needs an output schema" in e for e in _errors(packs_copy))


def test_lint_write_must_be_known_and_in_connectors(packs_copy: Path) -> None:
    path = packs_copy / "vuln/playbooks/weekly-vuln-triage.yaml"
    data = yaml.safe_load(path.read_text())
    data["steps"][2]["writes"] = ["jira.issues.explode", "gitlab.issues.create"]
    path.write_text(yaml.safe_dump(data))
    errors = _errors(packs_copy)
    assert any("jira.issues.explode: operation not declared" in e for e in errors)
    assert any("gitlab.issues.create: connector 'gitlab' is not in the playbook connectors" in e for e in errors)


def test_lint_rule_cannot_lower_class(packs_copy: Path) -> None:
    path = packs_copy / "ticketing/connectors/logos.yaml"
    data = yaml.safe_load(path.read_text())
    data["operations"]["rules"][2]["class"] = "write"  # accounts.disable
    path.write_text(yaml.safe_dump(data))
    assert any("lower than the platform floor 'destructive'" in e for e in _errors(packs_copy))


def test_lint_glob_rule_only_for_read(packs_copy: Path) -> None:
    path = packs_copy / "dev/connectors/gitlab.yaml"
    data = yaml.safe_load(path.read_text())
    data["operations"]["rules"].insert(0, {"match": "merge_requests.*", "class": "write", "exposed": True})
    path.write_text(yaml.safe_dump(data))
    assert any("globs apply only to read operations" in e for e in _errors(packs_copy))


def test_lint_duplicate_handle(packs_copy: Path) -> None:
    _edit(packs_copy / "dev/agents/reviewer.yaml", handle="dev-engineer")
    assert any("handle @dev-engineer already used" in e for e in _errors(packs_copy))


def test_lint_skill_name_must_match_directory(packs_copy: Path) -> None:
    path = packs_copy / "dev/skills/fix-issue/SKILL.md"
    path.write_text(path.read_text().replace("name: fix-issue", "name: fix-bug"))
    assert any("does not match directory" in e for e in _errors(packs_copy))


def test_openapi_rules_must_name_operations_of_the_spec(tmp_path: Path) -> None:
    from z_common.packs.lint import spec_problems

    (tmp_path / "connectors").mkdir()
    (tmp_path / "connectors" / "api.json").write_text(
        '{"openapi": "3.0.3", "info": {"title": "t", "version": "1"},'
        ' "paths": {"/items": {"get": {"operationId": "items.list"}}}}'
    )
    rules = {"rules": [{"match": "items.list", "class": "read"}, {"match": "items.purge", "class": "destructive"}]}
    manifest = {"name": "x", "kind": "openapi", "spec": "./api.json", "operations": rules}
    assert spec_problems(manifest, tmp_path) == ["rule 'items.purge' names no operation of ./api.json"]
    assert spec_problems({**manifest, "spec": "builtin:anything"}, tmp_path)[0].startswith("spec 'builtin:anything'")
    assert spec_problems({**manifest, "spec": "./missing.json"}, tmp_path) == [
        "spec './missing.json' not found next to the connector"
    ]
    assert spec_problems({**manifest, "spec": "https://example.test/openapi.json"}, tmp_path) == []


def test_workplace_m365_operations_come_from_the_graph_subset(repo: Path) -> None:
    from z_common.connectors.operations import materialize

    path = repo / "packs" / "workplace" / "connectors" / "m365.yaml"
    ops = {o.local_id: o for o in materialize(yaml.safe_load(path.read_text()), path.parent)}
    agent_tools = {
        tool.removeprefix("gateway:m365.")
        for agent in (repo / "packs" / "workplace" / "agents").glob("*.yaml")
        for tool in yaml.safe_load(agent.read_text()).get("tools", [])
        if tool.startswith("gateway:m365.")
    }
    assert agent_tools and agent_tools <= set(ops)
    assert all(ops[t].source_ref.split(" ")[0] in {"GET", "POST"} for t in agent_tools)
    assert ops["mail.reply"].source_ref == "POST /me/messages/{message_id}/reply"
    assert ops["mail.delete"].op_class == OperationClass.destructive and not ops["mail.delete"].proposed_exposed
