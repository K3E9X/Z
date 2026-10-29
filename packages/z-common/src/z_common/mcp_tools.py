"""The tools of Z as an MCP server (docs/03 §5 "MCP"), shared by `/mcp` on z-server and `z mcp serve`.

Each tool is one REST contract operation called with the caller's own credential, so permissions, validation,
audit and rate limits are exactly those of the API. Nothing here approves anything: approvals stay in Review.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_TEXT = 60_000


@dataclass(frozen=True)
class ZTool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool


TOOLS: tuple[ZTool, ...] = (
    ZTool(
        "start_mission",
        "Start a mission",
        "Start a Z mission from a free-form goal (`prompt`) or a playbook with its inputs. Agents only read; every "
        "external write they propose waits for a human approval in Z Review. Returns the mission id and its link.",
        {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What the mission should achieve"},
                "playbook": {"type": "string", "description": "Playbook name (see list_playbooks)"},
                "inputs": {"type": "object", "description": "Playbook inputs"},
                "title": {"type": "string"},
                "agents": {"type": "array", "items": {"type": "string"}, "description": "Agent handles, no @"},
                "data_class": {"type": "string", "enum": ["C0", "C1", "C2", "C3"]},
                "plan_first": {"type": "boolean", "description": "Draft the plan and wait for a human to start it"},
            },
            "additionalProperties": False,
        },
        read_only=False,
    ),
    ZTool(
        "get_mission",
        "Get a mission",
        "Status, progress, budget and outcome of a Z mission, with its jobs.",
        {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Mission id"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        read_only=True,
    ),
    ZTool(
        "list_playbooks",
        "List playbooks",
        "Playbooks that start a mission with known inputs.",
        {
            "type": "object",
            "properties": {"q": {"type": "string", "description": "Search text"}, "domain": {"type": "string"}},
            "additionalProperties": False,
        },
        read_only=True,
    ),
    ZTool(
        "list_review",
        "List what waits for review",
        "Proposed actions, questions and results waiting for a human in Z Review (approve them in Z).",
        {
            "type": "object",
            "properties": {
                "mission": {"type": "string", "description": "Only this mission"},
                "kind": {"type": "string", "description": "action, question or result"},
            },
            "additionalProperties": False,
        },
        read_only=True,
    ),
)
BY_NAME = {t.name: t for t in TOOLS}


class ToolInputError(ValueError):
    pass


@dataclass(frozen=True)
class Call:
    """One REST request; `then` is a second GET whose result is merged (get_mission adds its jobs)."""

    method: str
    path: str
    params: dict[str, Any] | None = None
    body: dict[str, Any] | None = None
    then: str | None = None


def _id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not ID.match(value):
        raise ToolInputError(f"{what} is an id (letters, digits, _ and -)")
    return quote(value, safe="")


def _strings(args: dict[str, Any], *names: str) -> dict[str, Any]:
    out = {}
    for name in names:
        value = args.get(name)
        if value is not None:
            if not isinstance(value, str) or len(value) > 200:
                raise ToolInputError(f"{name} is a short string")
            out[name] = value
    return out


def call_for(name: str, args: dict[str, Any] | None) -> Call:
    args = dict(args or {})
    tool = BY_NAME.get(name)
    if tool is None:
        raise ToolInputError(f"unknown tool {name}")
    unknown = set(args) - set(tool.input_schema["properties"])
    if unknown:
        raise ToolInputError(f"unknown arguments: {', '.join(sorted(unknown))}")
    if name == "start_mission":
        if not args.get("prompt") and not args.get("playbook"):
            raise ToolInputError("give a prompt or a playbook")
        return Call("POST", "/missions", body=args)
    if name == "get_mission":
        mission = _id(args.get("id"), "id")
        return Call("GET", f"/missions/{mission}", then=f"/missions/{mission}/jobs")
    if name == "list_playbooks":
        return Call("GET", "/playbooks", params={**_strings(args, "q", "domain"), "limit": 50})
    params = _strings(args, "kind")
    if args.get("mission") is not None:
        params["mission"] = _id(args["mission"], "mission")
    return Call("GET", "/review", params={**params, "limit": 50})


MISSION_FIELDS = (
    "id",
    "title",
    "status",
    "waiting_reason",
    "data_class",
    "playbook",
    "jobs",
    "pending_actions",
    "pending_questions",
    "budget",
    "tokens_in",
    "tokens_out",
    "created_at",
    "started_at",
    "ended_at",
    "error",
)
JOB_FIELDS = ("id", "step_id", "agent", "item_key", "status", "needs_attention_reason", "attempt", "error", "output")


def result_text(name: str, body: Any, extra: Any, web_url: str) -> str:
    """Compact JSON for the model: the fields that matter and a link to the mission in Z."""
    out: Any = body
    if name in {"start_mission", "get_mission"} and isinstance(body, dict):
        out = {k: body[k] for k in MISSION_FIELDS if k in body}
        out["url"] = f"{web_url.rstrip('/')}/missions/{body.get('id')}"
        if isinstance(extra, dict):
            out["job_list"] = [{k: j[k] for k in JOB_FIELDS if k in j} for j in extra.get("items") or []]
    elif isinstance(body, dict) and "items" in body:
        out = {"items": body["items"], "more": bool(body.get("next_cursor"))}
    text = json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=str)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…(truncated)"


def problem_text(status: int, body: Any) -> str:
    if isinstance(body, dict) and (body.get("detail") or body.get("title")):
        return f"Z refused ({status}): {body.get('detail') or body.get('title')}"
    return f"Z refused ({status})"
