"""`cli` connectors (docs/05 §1, ADR 0025): a declared operation is an argv template run in a sandbox container.

There is no shell. `{{args.<name>}}` placeholders are replaced inside argv elements; each value must be a scalar,
must not start with `-` (an argument can never become a flag), and cannot hold NUL or newlines. The environment of
the tool takes literals and `{{auth.secret}}`, `{{auth.username}}`, `{{auth.password}}` from the credential only;
arguments never reach the environment.
"""

from __future__ import annotations

import re
from typing import Any

ARG = re.compile(r"\{\{\s*args\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
AUTH = re.compile(r"\{\{\s*auth\.(secret|username|password)\s*\}\}")
ANY_PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/:-]*@sha256:[0-9a-f]{64}$")
MAX_ARG_CHARS = 4096
MAX_ARGV = 64


class CliError(ValueError):
    pass


def _value(name: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if not isinstance(value, str):
        raise CliError(f"argument {name} must be a string, number or boolean")
    if value.startswith("-"):
        raise CliError(f"argument {name} cannot start with '-'")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise CliError(f"argument {name} cannot contain NUL or line breaks")
    if len(value) > MAX_ARG_CHARS:
        raise CliError(f"argument {name} is longer than {MAX_ARG_CHARS} characters")
    return value


def render_argv(command: list[str], args: dict[str, Any]) -> list[str]:
    """The argv of one call. An element whose placeholders all have no value is dropped when it is only that
    placeholder (an optional argument); a missing value inside a larger element is an error."""
    out: list[str] = []
    for element in command:
        names = ARG.findall(element)
        if not names:
            out.append(element)
            continue
        missing = [n for n in names if args.get(n) is None]
        if missing:
            if ARG.fullmatch(element.strip()):
                continue  # optional argument left out
            raise CliError(f"missing argument {', '.join(missing)}")
        out.append(ARG.sub(lambda m: _value(m.group(1), args[m.group(1)]), element))
    return out


def render_env(env: dict[str, str], auth: dict[str, str]) -> dict[str, str]:
    """auth: {secret, username, password} of the credential; values never come from the agent."""

    def fill(m: re.Match[str]) -> str:
        if not auth.get(m.group(1)):
            raise CliError(f"the credential has no {m.group(1)}")
        return auth[m.group(1)]

    return {name: AUTH.sub(fill, value) for name, value in env.items()}


def connector_problems(manifest: dict[str, Any]) -> list[str]:
    """Static checks of a cli connector (pack lint and the connector API)."""
    if manifest.get("kind") != "cli":
        return []
    cli = manifest.get("cli") or {}
    problems = []
    image = cli.get("image")
    if not isinstance(image, str) or not DIGEST.match(image):
        problems.append("cli.image must be pinned by digest (name@sha256:...)")
    for name, value in (cli.get("env") or {}).items():
        if not ENV_NAME.match(str(name)):
            problems.append(f"cli.env {name}: environment names are upper case letters, digits and _")
        leftovers = ANY_PLACEHOLDER.findall(AUTH.sub("", str(value)))
        if leftovers:
            problems.append(f"cli.env {name}: only {{{{auth.secret|username|password}}}} placeholders are allowed")
    for op in manifest.get("declared") or []:
        op_id, command = op.get("id"), op.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
            problems.append(f"operation {op_id}: cli operations declare `command` (a list of strings)")
            continue
        if len(command) > MAX_ARGV:
            problems.append(f"operation {op_id}: at most {MAX_ARGV} command elements")
        declared = set(((op.get("input_schema") or {}).get("properties") or {}).keys())
        used = {n for element in command for n in ARG.findall(element)}
        if missing := sorted(used - declared):
            problems.append(f"operation {op_id}: the command uses undeclared arguments {', '.join(missing)}")
        for element in command:
            if AUTH.search(element):
                problems.append(f"operation {op_id}: credentials go in cli.env, never in the command")
            if ANY_PLACEHOLDER.findall(ARG.sub("", AUTH.sub("", element))):
                problems.append(f"operation {op_id}: unknown placeholder in {element!r}")
    return problems
