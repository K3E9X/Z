"""Step output: `result.json` in the workspace, validated against `spec.output.schema` (docs/04 §4 "Output repair")."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from z_common.models.common import FieldError

from z_runner.workspace import JailError, Workspace

_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)

# Output field that only Z can fill: the artifact id of the patch of the workspace, which Z builds itself from the
# agent's changes (a step that changes a repository hands it to the step that proposes the merge request).
PATCH_FIELD = "patch_artifact"


def fills_patch(schema: Mapping[str, Any]) -> bool:
    """True when the output schema has a top-level `patch_artifact` property (the runner sets it)."""
    properties = schema.get("properties")
    return isinstance(properties, Mapping) and PATCH_FIELD in properties


class OutputSchemaError(ValueError):
    """The step's output schema itself is invalid (a playbook bug, not an agent mistake)."""


@dataclass
class OutputCheck:
    output: dict[str, Any] | None
    errors: list[FieldError] = field(default_factory=list)
    recovered_from_message: bool = False

    @property
    def ok(self) -> bool:
        return self.output is not None and not self.errors


def json_from_message(message: str | None) -> dict[str, Any] | None:
    """A JSON object given as the final message (whole message or a fenced block)."""
    if not message:
        return None
    candidates = [message.strip(), *_FENCED_JSON.findall(message)]
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def check_output(
    workspace: Workspace,
    filename: str,
    schema: dict[str, Any],
    final_message: str | None,
    *,
    set_fields: Mapping[str, Any] | None = None,
) -> OutputCheck:
    """Load and validate the result. `set_fields` are fields the platform owns: they replace whatever the agent
    wrote (None removes the field) before validation, and the file is rewritten with them."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise OutputSchemaError(f"invalid output schema: {exc.message}") from exc
    try:
        path = workspace.resolve(filename)
    except JailError as exc:
        return OutputCheck(None, [FieldError(path="(file)", message=str(exc))])
    recovered = False
    if not path.is_file() or path.is_symlink():
        fallback = json_from_message(final_message)
        if fallback is None:
            return OutputCheck(
                None, [FieldError(path="(file)", message=f"{filename} was not written in the workspace root")]
            )
        path.write_text(json.dumps(fallback, indent=2, ensure_ascii=False), encoding="utf-8")
        recovered = True
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return OutputCheck(None, [FieldError(path="(file)", message=f"{filename} is not valid JSON: {exc}")])
    if not isinstance(value, dict):
        return OutputCheck(None, [FieldError(path="(root)", message="the result must be a JSON object")])
    if set_fields:
        for key, field_value in set_fields.items():
            if field_value is None:
                value.pop(key, None)
            else:
                value[key] = field_value
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    errors = [
        FieldError(path="/".join(str(p) for p in err.absolute_path) or "(root)", message=err.message)
        for err in sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
    ]
    return OutputCheck(value, errors[:50], recovered)
