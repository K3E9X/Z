"""Rules engine of the fake LLM: match a chat request, return the next scripted step."""

from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ToolCallSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    arguments: dict[str, Any] | str = Field(default_factory=dict)  # a string is sent verbatim (malformed JSON)
    id: str | None = None


class ErrorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: int = 500
    message: str = "fake error"
    type: str = "server_error"


class Step(BaseModel):
    """One scripted response. Exactly one of text / tool_calls / json / error / delay_ms is expected,
    except that `text` may accompany `tool_calls` (assistant content plus calls)."""

    model_config = ConfigDict(extra="forbid")
    text: str | None = None
    tool_calls: list[ToolCallSpec] | None = None
    json_: Any = Field(default=None, alias="json")
    delay_ms: int | None = None
    then: Step | None = None
    error: ErrorSpec | None = None
    finish_reason: str | None = None  # override, e.g. "length"


class Match(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None  # regex, full match
    contains: str | None = None  # regex searched in all message contents
    system_contains: str | None = None  # regex searched in system message contents
    last_role: Literal["user", "tool", "assistant", "system"] | None = None
    has_tools: bool | None = None


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: f"rule-{uuid.uuid4().hex[:8]}")
    match: Match = Field(default_factory=Match)
    responses: list[Step] = Field(min_length=1)
    repeat: bool = False


DEFAULT_STEP = Step(text="ok")


def content_text(content: Any) -> str:
    """Flatten an OpenAI message content (string or list of parts) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


def _matches(match: Match, body: dict[str, Any]) -> bool:
    messages: list[dict[str, Any]] = body.get("messages") or []
    if match.model is not None and not re.fullmatch(match.model, str(body.get("model", ""))):
        return False
    if match.contains is not None:
        text = "\n".join(content_text(m.get("content")) for m in messages)
        if not re.search(match.contains, text, re.S):
            return False
    if match.system_contains is not None:
        text = "\n".join(content_text(m.get("content")) for m in messages if m.get("role") in ("system", "developer"))
        if not re.search(match.system_contains, text, re.S):
            return False
    if match.last_role is not None and (not messages or messages[-1].get("role") != match.last_role):
        return False
    return match.has_tools is None or bool(body.get("tools")) == match.has_tools


class RuleBook:
    """Thread-safe ordered rules; rules added later take precedence."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rules: list[Rule] = []
        self._used: dict[str, int] = {}

    def add(self, rules: list[Rule]) -> list[str]:
        with self._lock:
            for rule in rules:
                self._rules = [r for r in self._rules if r.id != rule.id]
                self._used.pop(rule.id, None)
                self._rules.append(rule)
        return [r.id for r in rules]

    def clear(self) -> None:
        with self._lock:
            self._rules.clear()
            self._used.clear()

    def dump(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{**r.model_dump(by_alias=True), "used": self._used.get(r.id, 0)} for r in self._rules]

    def next_step(self, body: dict[str, Any]) -> tuple[str | None, Step]:
        with self._lock:
            for rule in reversed(self._rules):
                if not _matches(rule.match, body):
                    continue
                used = self._used.get(rule.id, 0)
                if used >= len(rule.responses) and not rule.repeat:
                    continue  # exhausted: fall through to older rules / default
                self._used[rule.id] = used + 1
                return rule.id, rule.responses[min(used, len(rule.responses) - 1)]
        return None, DEFAULT_STEP


def parse_rules(data: Any) -> list[Rule]:
    """Accept one rule, a list of rules, or {"rules": [...]}."""
    if isinstance(data, dict) and "rules" in data and "responses" not in data:
        data = data["rules"]
    if isinstance(data, dict):
        data = [data]
    return [Rule.model_validate(item) for item in data or []]


def load_rules_file(path: str | Path) -> list[Rule]:
    return parse_rules(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
