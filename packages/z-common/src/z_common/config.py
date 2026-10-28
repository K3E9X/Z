"""Load config/z.yaml: `${VAR}` / `${VAR:-default}` substitution, contract validation, typed access.

Substitution happens on string values after YAML parsing, so secrets with YAML-special characters stay
intact. A value that is exactly one placeholder is re-typed (`${N:-1024}` becomes the int 1024).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from z_common.contracts import ContractError, errors

PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def substitute(value: Any, env: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: substitute(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, env) for v in value]
    if not isinstance(value, str) or "${" not in value:
        return value

    def repl(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        found = env.get(name, "")
        return found if found != "" else (default or "")

    whole = PLACEHOLDER.fullmatch(value)
    result = PLACEHOLDER.sub(repl, value)
    if whole:
        typed = yaml.safe_load(result) if result else ""
        if isinstance(typed, bool | int | float):
            return typed
    return result


def load_config(path: str | Path, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Parse, substitute and validate. Raises ContractError with every problem found."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    data = substitute(raw, os.environ if env is None else env)
    problems = errors("config.schema.json", data)
    if problems:
        raise ContractError("config.schema.json", problems)
    result: dict[str, Any] = data
    return result
