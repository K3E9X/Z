"""Browser flows (docs/05 §1, connector `flows`): validation, placeholder rendering and the host allowlist,
shared by the pack linter, the gateway and the z-browser worker.

A flow is reviewed by an admin before it is exposed, so what it clicks is fixed: agent arguments can only fill
values and URL parts, never choose a selector. Credentials (`{{auth.username}}`, `{{auth.password}}`) can only
be typed into a field; they are never part of a URL, an assertion or an extracted value.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any
from urllib.parse import quote, urljoin, urlparse

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
NAME = re.compile(r"^(args|auth)\.([A-Za-z_][A-Za-z0-9_]*)$")
AUTH_FIELDS = {"username", "password"}
URL_FIELDS = {"url", "start_url"}

NEEDS: dict[str, tuple[str, ...]] = {
    "goto": ("url",),
    "click": ("selector",),
    "fill": ("selector", "value"),
    "select": ("selector", "value"),
    "check": ("selector",),
    "uncheck": ("selector",),
    "press": ("key",),
    "wait_for": ("selector",),
    "wait_for_url": ("url",),
    "extract_text": ("selector", "name"),
    "extract_table": ("selector", "name"),
    "extract_attribute": ("selector", "attribute", "name"),
    "assert_text": ("value",),
    "assert_visible": ("selector",),
    "screenshot": (),
}
# where each placeholder family may appear: (action, field)
ARGS_OK = {("goto", "url"), ("wait_for_url", "url"), ("fill", "value"), ("select", "value"), ("assert_text", "value")}
AUTH_OK = {("fill", "value")}


class FlowError(ValueError):
    pass


def placeholders(text: str) -> list[tuple[str, str]]:
    out = []
    for raw in PLACEHOLDER.findall(text):
        match = NAME.match(raw)
        if match is None:
            raise FlowError(f"unknown placeholder {{{{{raw}}}}}: use {{{{args.<name>}}}} or {{{{auth.username}}}}")
        family, name = match.group(1), match.group(2)
        if family == "auth" and name not in AUTH_FIELDS:
            raise FlowError(f"unknown credential field auth.{name}: use auth.username or auth.password")
        out.append((family, name))
    return out


def check_flow(flow: dict[str, Any]) -> set[str]:
    """Raise FlowError when a step misses a field or uses a placeholder where it is not allowed. Returns the
    argument names the flow uses."""
    used: set[str] = set()
    for family, name in placeholders(str(flow.get("start_url") or "")):
        if family == "auth":
            raise FlowError("start_url cannot contain a credential")
        used.add(name)
    outputs: set[str] = set()
    for i, step in enumerate(flow.get("steps") or [], start=1):
        action = str(step.get("action"))
        missing = [f for f in NEEDS.get(action, ()) if not step.get(f)]
        if missing:
            raise FlowError(f"step {i} ({action}) needs {', '.join(missing)}")
        for field, value in step.items():
            if not isinstance(value, str):
                continue
            for family, name in placeholders(value):
                allowed = AUTH_OK if family == "auth" else ARGS_OK
                if (action, field) not in allowed:
                    where = "typed into a field" if family == "auth" else "used in a URL or a typed value"
                    raise FlowError(f"step {i} ({action}.{field}): {family}.{name} can only be {where}")
                if family == "args":
                    used.add(name)
        if action.startswith("extract_"):
            if step["name"] in outputs:
                raise FlowError(f"step {i}: output {step['name']!r} is extracted twice")
            outputs.add(str(step["name"]))
    return used


def render(text: str, args: dict[str, Any], auth: dict[str, str] | None = None, *, in_url: bool = False) -> str:
    def sub(match: re.Match[str]) -> str:
        found = NAME.match(match.group(1).strip())
        if found is None:
            raise FlowError(f"unknown placeholder {match.group(0)}")
        family, name = found.group(1), found.group(2)
        if family == "auth":
            value = (auth or {}).get(name)
            if value is None:
                raise FlowError(f"the credential has no {name}")
            return value
        if name not in args or args[name] is None:
            raise FlowError(f"missing argument {name}")
        value = args[name]
        text_value = str(value).lower() if isinstance(value, bool) else str(value)
        return quote(text_value, safe="") if in_url else text_value

    return PLACEHOLDER.sub(sub, text)


def resolve_url(base_url: str | None, url: str) -> str:
    return urljoin((base_url or "").rstrip("/") + "/", url) if base_url else url


def hosts_for(base_url: str | None, extra: list[str] | None) -> list[str]:
    hosts = [h.lower() for h in (extra or []) if h]
    base = urlparse(base_url or "").hostname
    if base:
        hosts.insert(0, base.lower())
    return hosts


def host_allowed(url: str, hosts: list[str]) -> bool:
    """Only http(s) to an allowlisted host (exact, or `*.example.com` for subdomains)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == h or (h.startswith("*.") and fnmatch.fnmatch(host, h)) for h in hosts)


def connector_problems(manifest: dict[str, Any]) -> list[str]:
    """Cross-field checks a schema cannot express: every browser operation names an existing flow, every flow
    passes `check_flow`, and every argument a flow uses is declared in the operation's input schema."""
    flows = manifest.get("flows") or {}
    if flows and manifest.get("kind") != "browser":
        return ["flows are only used by browser connectors"]
    if manifest.get("kind") != "browser":
        return []
    problems = []
    used_by: dict[str, set[str]] = {}
    for name, flow in flows.items():
        try:
            used_by[name] = check_flow(flow)
        except FlowError as exc:
            problems.append(f"flow {name}: {exc}")
    for op in manifest.get("declared") or []:
        ref = op.get("flow")
        if not ref:
            problems.append(f"operation {op.get('id')}: browser operations name a flow")
            continue
        if ref not in flows:
            problems.append(f"operation {op.get('id')}: flow {ref!r} is not defined in flows")
            continue
        declared = set(((op.get("input_schema") or {}).get("properties") or {}).keys())
        missing = sorted(used_by.get(ref, set()) - declared)
        if missing:
            problems.append(f"operation {op.get('id')}: flow {ref} uses undeclared arguments {', '.join(missing)}")
    return problems
