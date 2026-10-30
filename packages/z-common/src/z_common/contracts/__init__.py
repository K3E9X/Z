"""Load and apply the JSON Schemas and the OpenAPI document in `contracts/`.

The contracts directory is found from `Z_CONTRACTS_DIR`, else by walking up from the current
directory and from this file until a directory containing `contracts/openapi.yaml` is found.
"""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

BASE_URI = "https://schemas.z.example/contracts/"

SCHEMA_FILES = (
    "common.schema.json",
    "agent.schema.json",
    "playbook.schema.json",
    "connector.schema.json",
    "pack.schema.json",
    "skill-frontmatter.schema.json",
    "objective.schema.json",
    "eval-case.schema.json",
    "mission-plan.schema.json",
    "action.schema.json",
    "events.schema.json",
    "config.schema.json",
)


class ContractError(Exception):
    """A document does not match its contract."""

    def __init__(self, schema: str, errors: list[str]) -> None:
        super().__init__(f"{schema}: " + "; ".join(errors))
        self.schema = schema
        self.errors = errors


def _candidates() -> Iterator[Path]:
    env = os.environ.get("Z_CONTRACTS_DIR")
    if env:
        yield Path(env)
    for start in (Path.cwd(), Path(__file__).resolve()):
        for parent in (start, *start.parents):
            yield parent / "contracts"


@functools.cache
def contracts_dir() -> Path:
    for candidate in _candidates():
        if (candidate / "openapi.yaml").is_file():
            return candidate
    raise FileNotFoundError("contracts/ not found; set Z_CONTRACTS_DIR")


@functools.cache
def load_schema(name: str) -> dict[str, Any]:
    """Load a schema by file name, e.g. `agent.schema.json`."""
    data: dict[str, Any] = json.loads((contracts_dir() / name).read_text(encoding="utf-8"))
    return data


@functools.cache
def registry() -> Registry[Any]:
    resources = [(BASE_URI + name, Resource.from_contents(load_schema(name), DRAFT202012)) for name in SCHEMA_FILES]
    return Registry().with_resources(resources)


@functools.cache
def validator(name: str, pointer: str = "") -> Draft202012Validator:
    """Validator for a whole schema file or for a `$defs` entry (pointer like `/$defs/Step`)."""
    schema: dict[str, Any] = {"$ref": BASE_URI + name + ("#" + pointer if pointer else "")}
    return Draft202012Validator(schema, registry=registry(), format_checker=Draft202012Validator.FORMAT_CHECKER)


def errors(name: str, document: Any, pointer: str = "") -> list[str]:
    """Return human-readable validation errors (empty when valid)."""
    found: list[ValidationError] = sorted(validator(name, pointer).iter_errors(document), key=lambda e: list(e.path))
    out = []
    for err in found:
        location = "/".join(str(p) for p in err.absolute_path) or "(root)"
        out.append(f"{location}: {err.message}")
    return out


def validate(name: str, document: Any, pointer: str = "") -> None:
    """Raise ContractError when `document` does not match the schema."""
    problems = errors(name, document, pointer)
    if problems:
        raise ContractError(name + pointer, problems)


@functools.cache
def api_registry() -> Registry[Any]:
    """Registry with the OpenAPI document too, so response schemas can be validated with their $refs."""
    combined: Registry[Any] = registry().with_resource(
        BASE_URI + "openapi.yaml", Resource.from_contents(openapi(), DRAFT202012)
    )
    return combined


def response_errors(operation_id: str, status: int, body: Any) -> list[str] | None:
    """Validation errors of a JSON response against the contract, or None when the contract has no JSON
    schema for that operation and status (binary, SSE, problem details checked separately)."""
    for path, item in openapi()["paths"].items():
        for method, op in item.items():
            if op["operationId"] != operation_id:
                continue
            response = op["responses"].get(str(status))
            schema = ((response or {}).get("content") or {}).get("application/json", {}).get("schema")
            if schema is None:
                return None
            pointer = "/".join(
                [
                    "paths",
                    path.replace("~", "~0").replace("/", "~1"),
                    method,
                    "responses",
                    str(status),
                    "content",
                    "application~1json",
                    "schema",
                ]
            )
            validator = Draft202012Validator({"$ref": f"{BASE_URI}openapi.yaml#/{pointer}"}, registry=api_registry())
            return [
                f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
                for e in validator.iter_errors(body)
            ]
    raise KeyError(operation_id)


@functools.cache
def openapi() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((contracts_dir() / "openapi.yaml").read_text(encoding="utf-8"))
    return data


@functools.cache
def permissions() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((contracts_dir() / "permissions.yaml").read_text(encoding="utf-8"))
    return data


def load_document(path: Path) -> Any:
    """Load a JSON or YAML document."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    return yaml.safe_load(text)
