"""Sandboxed templates for prompts, repos and trigger inputs (contracts/README.md "Templates").

`{{ inputs.x }}`, `{{ item.x }}`, `{{ steps.<id>.output.x }}`, `{{ event.x }}`. Missing values are errors
(StrictUndefined), the sandbox refuses attribute access to internals, and output is plain text (no HTML).
"""

from __future__ import annotations

import json
from typing import Any

from jinja2 import StrictUndefined, TemplateError, UndefinedError
from jinja2.sandbox import ImmutableSandboxedEnvironment

_env = ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
_env.filters["json"] = lambda v: json.dumps(v, ensure_ascii=False, default=str)


class TemplateFailure(ValueError):
    pass


def render(template: str, context: dict[str, Any]) -> str:
    try:
        return _env.from_string(template).render(**context)
    except UndefinedError as exc:
        raise TemplateFailure(f"missing value in template: {exc.message}") from exc
    except (TemplateError, OverflowError, RecursionError, TypeError) as exc:
        raise TemplateFailure(f"invalid template: {exc}") from exc


def render_value(value: Any, context: dict[str, Any]) -> Any:
    """Render every string inside a JSON-like value; a string that is exactly one `{{ expr }}` keeps its type."""
    if isinstance(value, dict):
        return {k: render_value(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, context) for v in value]
    if not isinstance(value, str) or "{{" not in value:
        return value
    stripped = value.strip()
    if stripped.startswith("{{") and stripped.endswith("}}") and stripped.count("{{") == 1:
        try:
            return _env.compile_expression(stripped[2:-2].strip(), undefined_to_none=False)(**context)
        except UndefinedError as exc:
            raise TemplateFailure(f"missing value in template: {exc.message}") from exc
        except (TemplateError, OverflowError, RecursionError, TypeError) as exc:
            raise TemplateFailure(f"invalid template: {exc}") from exc
    return render(value, context)
