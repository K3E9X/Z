"""Canonical operation ids from an OpenAPI document (docs/03 §4)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from z_common.contracts import load_document

METHODS = ("get", "head", "post", "put", "patch", "delete")
VERB_BY_METHOD = {"post": "create", "put": "update", "patch": "update", "delete": "delete"}
API_PREFIX = re.compile(r"^(api|rest)$|^v\d+(\.\d+)*$")


def slug(value: str) -> str:
    """Slug of an operationId: lower case, [a-z0-9_] segments separated by dots (`listFindings` -> `list_findings`)."""
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
    parts = [re.sub(r"[^a-z0-9]+", "_", part).strip("_") for part in value.split(".")]
    return ".".join(p for p in parts if p)


def _segments(path: str) -> list[str]:
    return [s for s in path.strip("/").split("/") if s]


def id_from_path(method: str, path: str) -> str:
    """`GET /api/v2/findings/` -> findings.list; `GET …/{id}/` -> findings.get; `POST /x/{id}/close` -> x.close."""
    method = method.lower()
    segments = [s for s in _segments(path) if not API_PREFIX.match(s)]
    params = [s.startswith("{") for s in segments]
    resources = [slug(s).replace(".", "_") for s in segments if not s.startswith("{")]
    if not resources:
        return "root." + ("get" if method in {"get", "head"} else VERB_BY_METHOD.get(method, method))
    ends_with_param = bool(params) and params[-1]
    if method == "post" and not ends_with_param and len(segments) >= 2 and params[-2]:
        # POST /x/{id}/<action>
        return ".".join([*resources[:-1], resources[-1]])
    read_verb = "get" if ends_with_param else "list"
    verb = read_verb if method in {"get", "head"} else VERB_BY_METHOD.get(method, method)
    return ".".join([*resources, verb])


def operations_from_spec(path: Path, mode: str) -> dict[str, str]:
    """Local operation id -> HTTP method for every operation of the spec."""
    spec: dict[str, Any] = load_document(path)
    result: dict[str, str] = {}
    for route, item in (spec.get("paths") or {}).items():
        for method in METHODS:
            operation = item.get(method) if isinstance(item, dict) else None
            if not isinstance(operation, dict):
                continue
            if mode == "from_operation_id" and operation.get("operationId"):
                local_id = slug(str(operation["operationId"]))
            else:
                local_id = id_from_path(method, route)
            result[local_id] = method.upper()
    return result
