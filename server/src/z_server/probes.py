"""Synthetic tool-call probe per alias (docs/06 §5, ADR 0034).

Every `inference.probes.tool_call_probe_s` (default 600 s, at least 60), one replica sends each internal chat
alias one small chat completion with one tool, through the LiteLLM gateway only (invariant 1) and with the
platform key (internal aliases only, so nothing leaves the cluster), and checks the answer is a tool call that
parses, names the tool and carries the expected argument. It is the single-call check of
`poc/check_tool_calling.py`, the gate every alias passes before Z uses it.

The outcome of each completed probe is kept in `settings` (`tool_call_probes`), the last WINDOW per alias, so the
rate survives restarts. A gateway error (unreachable, non-200) says nothing about tool-call parsing: it is kept
as the alias' last error but stays out of the rate. The Inference screen shows, per alias, `tool_call_ok` (the
last probe), `tool_call_parse_error_rate` (failures over the kept probes) and `last_probe_at`.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import DataClass

from z_server import usage
from z_server.db.models import Setting
from z_server.llm import chat
from z_server.llm.inference import routing_config
from z_server.llm.routing import allowed_aliases
from z_server.memory import EMBED_ALIAS
from z_server.state import AppState

log = logging.getLogger("z.probes")
STATE_KEY = "tool_call_probes"
LOCK_KEY = 0x5A_9B0BE  # pg_try_advisory_xact_lock key: one replica claims each probe round
DEFAULT_INTERVAL_S = 600
MIN_INTERVAL_S = 60
WINDOW = 20
TIMEOUT_S = 30.0
TOOL_NAME = "read_file"
EXPECTED = {"path": "src/app.py"}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Read a file from the repository and return its content.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative file path"}},
                "required": ["path"],
            },
        },
    }
]
PROMPT = "Read the file src/app.py."


def interval_s(config: dict[str, Any]) -> int:
    raw = ((config.get("inference") or {}).get("probes") or {}).get("tool_call_probe_s", DEFAULT_INTERVAL_S)
    try:
        return max(MIN_INTERVAL_S, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_S


async def load(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(Setting, STATE_KEY)
    return dict(row.value) if row is not None else {}


def summary(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per alias: tool_call_ok, tool_call_parse_error_rate, last_probe_at (the InferenceModel fields)."""
    out: dict[str, dict[str, Any]] = {}
    for alias, entry in (value.get("aliases") or {}).items():
        results = [bool(r) for r in entry.get("results") or []]
        if not results:
            continue
        out[alias] = {
            "tool_call_ok": results[-1],
            "tool_call_parse_error_rate": round(results.count(False) / len(results), 4),
            "last_probe_at": entry.get("last_probe_at"),
        }
    return out


async def _claim(state: AppState, at: datetime) -> tuple[str, list[str]] | None:
    """(platform key, aliases) when this replica runs the round that is due, else None."""
    async with state.sessions() as db, db.begin():
        if not (await db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY})).scalar():
            return None
        row = await db.get(Setting, STATE_KEY, with_for_update=True)
        value = dict(row.value) if row is not None else {}
        last = value.get("last_run_at")
        if isinstance(last, str) and datetime.fromisoformat(last) + timedelta(seconds=interval_s(state.config)) > at:
            return None
        aliases = [a for a in allowed_aliases(await routing_config(db, state.config), DataClass.C3) if a != EMBED_ALIAS]
        value["last_run_at"] = at.isoformat()
        if row is None:
            db.add(Setting(key=STATE_KEY, value=value, updated_by="system"))
        else:
            row.value, row.updated_by = value, "system"
        if not aliases:
            return None
        try:
            key = await chat.platform_key(db, state)
        except chat.ChatError as exc:
            log.warning("tool-call probe skipped: %s", exc)
            return None
    return key, aliases


def check(body: dict[str, Any]) -> tuple[bool, str]:
    """Whether a chat completion answer holds a parsable call of the probe tool with the expected arguments."""
    message = ((body.get("choices") or [{}])[0] or {}).get("message") or {}
    calls = message.get("tool_calls") or []
    if not calls:
        return False, "no tool call in the answer"
    function = (calls[0] or {}).get("function") or {}
    if function.get("name") != TOOL_NAME:
        return False, f"called {str(function.get('name'))[:40]!r} instead of {TOOL_NAME}"
    try:
        arguments = json.loads(function.get("arguments") or "")
    except (TypeError, ValueError):
        return False, "the tool call arguments are not JSON"
    if not isinstance(arguments, dict) or any(arguments.get(k) != v for k, v in EXPECTED.items()):
        return False, "the tool call arguments are not the expected ones"
    return True, "ok"


async def probe(state: AppState, key: str, alias: str) -> tuple[bool | None, str, int, int]:
    """(parsed ok, or None for a gateway error; detail; tokens in; tokens out)."""
    try:
        resp = await state.http.post(
            f"{state.settings.litellm_url.rstrip('/')}/v1/chat/completions",
            json={
                "model": alias,
                "messages": [{"role": "user", "content": PROMPT}],
                "tools": TOOLS,
                "tool_choice": "auto",
                "max_tokens": 200,
                "temperature": 0,
            },
            headers={"Authorization": f"Bearer {key}"},
            timeout=TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        return None, f"the model gateway is unreachable ({type(exc).__name__})", 0, 0
    if resp.status_code != 200:
        if resp.status_code == 401:  # the platform key was revoked: the next round creates a new one
            state.cache.pop("platform_key", None)
        return None, f"the model gateway answered {resp.status_code}", 0, 0
    try:
        body = resp.json()
    except ValueError:
        return False, "the answer is not JSON", 0, 0
    if not isinstance(body, dict):
        return False, "the answer is not a JSON object", 0, 0
    ok, detail = check(body)
    used = body.get("usage") or {}
    return ok, detail, int(used.get("prompt_tokens") or 0), int(used.get("completion_tokens") or 0)


async def tick(state: AppState, at: datetime | None = None) -> dict[str, bool | None]:
    """Run the probe round when it is due; returns {alias: ok (None for a gateway error)} for the aliases probed."""
    at = at or datetime.now(UTC)
    claimed = await _claim(state, at)
    if claimed is None:
        return {}
    key, aliases = claimed
    outcomes: dict[str, tuple[bool | None, str, datetime]] = {}
    spent: dict[str, tuple[int, int]] = {}
    for alias in aliases:
        started = time.monotonic()
        ok, detail, tokens_in, tokens_out = await probe(state, key, alias)
        outcomes[alias] = (ok, detail, datetime.now(UTC))
        spent[alias] = (tokens_in, tokens_out)
        log.info("tool-call probe %s: %s (%.1f s)", alias, detail, time.monotonic() - started)
    async with state.sessions() as db, db.begin():
        row = await db.get(Setting, STATE_KEY, with_for_update=True)
        value = dict(row.value) if row is not None else {}
        entries = dict(value.get("aliases") or {})
        for alias, (ok, detail, when) in outcomes.items():
            entry = dict(entries.get(alias) or {})
            entry["detail"], entry["checked_at"] = detail[:200], when.isoformat()
            if ok is not None:
                entry["results"] = [*list(entry.get("results") or []), ok][-WINDOW:]
                entry["last_probe_at"] = when.isoformat()
            entries[alias] = entry
        value["aliases"] = entries
        if row is None:
            db.add(Setting(key=STATE_KEY, value=value, updated_by="system"))
        else:
            row.value, row.updated_by = value, "system"
        for alias, (tokens_in, tokens_out) in spent.items():
            if tokens_in or tokens_out:
                await usage.record_platform(db, "probe", alias, "", "", tokens_in, tokens_out, at)
    return {alias: ok for alias, (ok, _, _) in outcomes.items()}
