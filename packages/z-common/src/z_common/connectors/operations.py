"""Operations a connector exposes, as far as they are known from its manifest (docs/03 §4, docs/05 §3).

- `operation_map` entries (MCP tool names or OpenAPI operationIds), `declared` operations, a local OpenAPI
  spec and exact-id rules are materialized here. Remote specs and MCP tool lists are discovered at runtime
  by the gateway and go through the same `decide()`.
- class = max(classifier floor, matching rule class); globs only match read operations.
- proposed_exposed comes from the first matching rule, else `default_exposed`. Nothing is exposed to
  agents until an admin approves (`exposed` starts False).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from z_common.connectors.openapi import METHODS, id_from_path, slug
from z_common.contracts import load_document
from z_common.models.enums import OperationClass
from z_common.packs.classify import classify, glob_match, is_glob, raise_only


@dataclass
class OperationSpec:
    local_id: str
    source_ref: str
    classifier_class: OperationClass
    op_class: OperationClass
    proposed_exposed: bool
    input_schema: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    method: str | None = None


def decide(
    manifest: dict[str, Any],
    local_id: str,
    method: str | None,
    read_only_hint: bool | None = None,
    *,
    require_hint: bool = False,
) -> tuple[OperationClass, OperationClass, bool]:
    """(classifier floor, effective class, proposed exposure) for one operation."""
    floor = classify(local_id, method, read_only_hint, require_hint=require_hint)
    policy = manifest.get("operations") or {}
    for rule in policy.get("rules") or []:
        match = str(rule["match"])
        hit = match == local_id or (is_glob(match) and floor == OperationClass.read and glob_match(match, local_id))
        if not hit:
            continue
        requested = OperationClass(rule["class"]) if rule.get("class") else None
        cls = raise_only(floor, requested)
        exposed = rule.get("exposed")
        return floor, cls, bool(policy.get("default_exposed", False)) if exposed is None else bool(exposed)
    return floor, floor, bool(policy.get("default_exposed", False))


def _openapi_input_schema(operation: dict[str, Any], path_item: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in [*(path_item.get("parameters") or []), *(operation.get("parameters") or [])]:
        if not isinstance(param, dict) or param.get("in") not in {"path", "query"}:
            continue
        properties[param["name"]] = param.get("schema") or {"type": "string"}
        if param.get("required") or param.get("in") == "path":
            required.append(param["name"])
    body = (((operation.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
    if isinstance(body, dict):
        properties["body"] = body
        if (operation.get("requestBody") or {}).get("required"):
            required.append("body")
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = sorted(set(required))
    return schema


def from_openapi(manifest: dict[str, Any], spec: dict[str, Any]) -> list[OperationSpec]:
    mode = manifest.get("operation_ids") or "from_operation_id"
    overrides = manifest.get("operation_map") or {}
    out = []
    for route, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId")
            if op_id in overrides:
                local_id = str(overrides[op_id])
            elif mode == "from_operation_id" and op_id:
                local_id = slug(str(op_id))
            else:
                local_id = id_from_path(method, route)
            floor, cls, exposed = decide(manifest, local_id, method.upper())
            out.append(
                OperationSpec(
                    local_id=local_id,
                    source_ref=f"{method.upper()} {route}",
                    classifier_class=floor,
                    op_class=cls,
                    proposed_exposed=exposed,
                    input_schema=_openapi_input_schema(operation, item),
                    description=str(operation.get("summary") or operation.get("description") or ""),
                    method=method.upper(),
                )
            )
    return out


def materialize(manifest: dict[str, Any], connector_dir: Path | None = None) -> list[OperationSpec]:
    """Operations known statically. Local OpenAPI specs are read from `connector_dir`."""
    found: dict[str, OperationSpec] = {}
    spec_ref = manifest.get("spec")
    if (
        manifest.get("kind") == "openapi"
        and isinstance(spec_ref, str)
        and connector_dir is not None
        and not spec_ref.startswith(("http", "$", "builtin:"))
    ):
        path = (connector_dir / spec_ref).resolve()
        if path.is_file():
            for op in from_openapi(manifest, load_document(path)):
                found[op.local_id] = op
    for declared in manifest.get("declared") or []:
        method = declared.get("method")
        floor, cls, exposed = decide(manifest, declared["id"], method)
        ref = (
            f"{method} {declared.get('path')}"
            if method
            else str(declared.get("query") or declared.get("command") or declared.get("flow") or declared["id"])
        )
        found[declared["id"]] = OperationSpec(
            declared["id"],
            ref,
            floor,
            cls,
            exposed,
            declared.get("input_schema") or {"type": "object"},
            declared.get("description", ""),
            method,
        )
    if manifest.get("kind") == "mcp":
        for tool, local_id in (manifest.get("operation_map") or {}).items():
            if local_id in found:
                continue
            floor, cls, exposed = decide(manifest, local_id, None)  # refined with MCP hints at discovery
            found[local_id] = OperationSpec(local_id, f"mcp:{tool}", floor, cls, exposed, {"type": "object"})
    for rule in (manifest.get("operations") or {}).get("rules") or []:
        match = str(rule["match"])
        if is_glob(match) or match in found:
            continue
        floor, cls, exposed = decide(manifest, match, None)
        found[match] = OperationSpec(match, f"{manifest.get('kind')}:{match}", floor, cls, exposed, {"type": "object"})
    return list(found.values())
