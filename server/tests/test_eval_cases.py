"""How eval cases become missions (docs/08 §6): output schema from the case's checks, fixtures as pack files or
literal text, the agent's connectors attached, and json_schema checks against contract schemas."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from z_common.models.manifests import Check, EvalCase
from z_server import evals
from z_server.errors import Problem

REPO = Path(__file__).resolve().parents[2]


def case(*checks: dict[str, Any]) -> EvalCase:
    return EvalCase.model_validate({"id": "c", "input": {"prompt": "do it"}, "checks": list(checks)})


def test_output_schema_comes_from_the_case() -> None:
    triage = case({"type": "json_schema", "schema": "schemas/triage-result.json"}, {"type": "no_write_tools"})
    assert evals.output_schema(triage, "vuln") == "vuln/schemas/triage-result.json"
    fields = case({"type": "field_equals", "path": "output.priority", "value": "low"})
    assert evals.output_schema(fields, "vuln") == evals.GENERIC_OUTPUT
    contract = case({"type": "json_schema", "schema": "contracts/agent.schema.json"})
    assert evals.output_schema(contract, "core") == evals.GENERIC_OUTPUT  # contract schemas are checked, not staged
    actions_only = case({"type": "count_actions", "operation": "jira.issues.create", "equals": 1})
    assert evals.output_schema(actions_only, "ticketing") is None
    assert (REPO / "packs" / evals.GENERIC_OUTPUT).is_file()


def test_fixtures_are_pack_files_or_literal_text() -> None:
    state: Any = SimpleNamespace(settings=SimpleNamespace(packs_path=REPO / "packs"))
    data = evals.fixture_bytes(state, "demo", "evals/fixtures/notes.md")
    assert data == (REPO / "packs/demo/evals/fixtures/notes.md").read_bytes()
    assert evals.fixture_bytes(state, "vuln", "internet-facing, prod") == b"internet-facing, prod"
    assert evals.fixture_bytes(state, "vuln", "../../etc/passwd") == b"../../etc/passwd"  # never read outside
    assert evals.fixture_bytes(state, "vuln", {"asset": "edge-01"}) == b'{"asset": "edge-01"}'
    with pytest.raises(Problem, match="directory"):
        evals.fixture_bytes(state, "demo", "evals/fixtures")


def test_eval_missions_get_the_connectors_the_agent_reads() -> None:
    agent: Any = SimpleNamespace(
        profile={"tools": ["read", "gateway:confluence.pages.*", "gateway:jira.issues.search", "gateway:@read"]}
    )
    assert evals.agent_connectors(agent) == {"confluence", "jira"}


async def test_json_schema_checks_resolve_contract_schemas() -> None:
    profile = {
        "name": "atlas",
        "display_name": "Atlas",
        "handle": "atlas",
        "role": "Triage",
        "instructions": "Triage findings.",
        "model": "z-fast",
        "version": "1.0.0",
        "skills": [],
        "tools": ["read"],
        "autonomy": "read_only",
        "limits": {"max_steps": 20, "max_tokens": 100000, "timeout_s": 600},
    }
    item = Check.model_validate({"type": "json_schema", "schema": "contracts/agent.schema.json"})
    ok, detail = await evals.check(None, None, "core", item, None, None, {"output": profile})  # type: ignore[arg-type]
    assert ok, detail
    bad, detail = await evals.check(None, None, "core", item, None, None, {"output": {"name": "x"}})  # type: ignore[arg-type]
    assert not bad and "required" in detail
