"""Contracts are valid, self-consistent and every API route declares a permission."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate as validate_openapi
from openapi_spec_validator.readers import read_from_filename
from z_common import contracts
from z_common.contracts.validate_examples import check

SPECIAL_PERMISSIONS = {"public", "authenticated", "hmac", "job_token", "service"}


@pytest.mark.parametrize("name", contracts.SCHEMA_FILES)
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(contracts.load_schema(name))


def _refs(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.append(value)
            else:
                found += _refs(value)
    elif isinstance(node, list):
        for item in node:
            found += _refs(item)
    return found


def _resolve(ref: str, base_file: str) -> Any:
    file, _, pointer = ref.partition("#")
    doc: Any = contracts.load_schema(file) if file else contracts.load_schema(base_file)
    if file.endswith("openapi.yaml"):
        doc = contracts.openapi()
    for part in [p for p in pointer.split("/") if p]:
        doc = doc[part.replace("~1", "/").replace("~0", "~")]
    return doc


@pytest.mark.parametrize("name", contracts.SCHEMA_FILES)
def test_every_ref_resolves(name: str) -> None:
    for ref in _refs(contracts.load_schema(name)):
        _resolve(ref, name)


def test_openapi_document_is_valid(repo: Path) -> None:
    spec, base_uri = read_from_filename(str(repo / "contracts" / "openapi.yaml"))
    validate_openapi(spec, base_uri=base_uri)


def test_openapi_refs_resolve() -> None:
    doc = contracts.openapi()
    schemas = doc["components"]["schemas"]
    for ref in _refs(doc):
        file, _, pointer = ref.partition("#")
        if not file:
            assert pointer.startswith("/components/schemas/"), ref
            assert pointer.removeprefix("/components/schemas/") in schemas, ref
        else:
            _resolve(ref, file)


def _operations() -> list[tuple[str, str, dict[str, Any]]]:
    doc = contracts.openapi()
    return [(path, method, op) for path, item in doc["paths"].items() for method, op in item.items()]


def test_every_route_declares_a_known_permission() -> None:
    known = set(contracts.permissions()["permissions"]) | SPECIAL_PERMISSIONS
    for path, method, op in _operations():
        assert op.get("x-permission") in known, f"{method.upper()} {path}: {op.get('x-permission')!r}"
        assert op.get("x-owner") in {f"B{i}" for i in range(1, 9)}, f"{method.upper()} {path}: owner"


def test_operation_ids_are_unique() -> None:
    ids = [op["operationId"] for _, _, op in _operations()]
    assert len(ids) == len(set(ids))


def test_mutating_routes_are_audited() -> None:
    exempt_internal = re.compile(r"^/internal/")
    for path, method, op in _operations():
        if method in {"post", "put", "patch", "delete"} and not exempt_internal.match(path):
            if op["x-permission"] in {"public"} and path in {"/auth/cli/exchange", "/auth/local/login"}:
                assert op.get("x-audit"), path
            elif op["operationId"] in {
                "draftPlan",
                "draftAgent",
                "testConnector",
                "importConnector",
                "adminDryRunGroupMapping",
                "adminTestAuthSettings",
                "adminVerifyAudit",
            }:
                continue
            else:
                assert op.get("x-audit"), f"{method.upper()} {path} must be audited"


def test_admin_writes_need_step_up() -> None:
    for path, method, op in _operations():
        if path.startswith("/admin/") and method in {"put", "patch", "delete"}:
            assert op.get("x-step-up"), f"{method.upper()} {path}"


def test_problem_responses_everywhere() -> None:
    for path, method, op in _operations():
        assert "default" in op["responses"], f"{method.upper()} {path}"


def test_permission_roles_reference_known_permissions() -> None:
    data = contracts.permissions()
    known = set(data["permissions"])
    roles = data["roles"]
    for role, perms in roles.items():
        for perm in perms:
            assert perm == "*" or perm in known or perm in roles, f"{role}: {perm}"


def test_examples_behave() -> None:
    assert check(contracts.contracts_dir()) == []


def test_example_config_validates_after_substitution(repo: Path) -> None:
    from z_common.config import load_config

    env = {"OIDC_ISSUER_URL": "https://gitlab.example.test", "LITELLM_URL": "http://litellm:4000", "Z_AUTH_MODE": "dev"}
    cfg = load_config(repo / "config" / "z.example.yaml", env)
    assert cfg["memory"]["embedding_dim"] == 1024
    assert cfg["secrets"]["backend"] == "env"
    assert cfg["auth"]["mode"] == "dev"


def test_config_rejects_sandbox_network_allow(repo: Path, tmp_path: Path) -> None:
    from z_common.config import load_config
    from z_common.contracts import ContractError

    data = yaml.safe_load((repo / "config" / "z.example.yaml").read_text())
    data["guardrails"]["sandbox_network"] = {"value": "allow"}
    bad = tmp_path / "z.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(ContractError):
        load_config(bad, {"LITELLM_URL": "x"})


def test_litellm_example_has_every_alias(repo: Path) -> None:
    litellm = yaml.safe_load((repo / "config" / "litellm.example.yaml").read_text())
    names = {m["model_name"] for m in litellm["model_list"]}
    cfg = yaml.safe_load((repo / "config" / "z.example.yaml").read_text())
    assert set(cfg["inference"]["aliases"]) <= names


def test_schemas_are_pretty_json(repo: Path) -> None:
    for name in contracts.SCHEMA_FILES:
        json.loads((repo / "contracts" / name).read_text())
