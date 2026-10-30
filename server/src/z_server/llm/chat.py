"""Platform-side model calls (plan drafting, session replies, judge model stage, eval grading).

Only through the LiteLLM gateway (invariant 1), with a dedicated platform virtual key restricted to the
internal aliases (C2/C3 safe whatever the routing policy), created on first use and renewed before it
expires. The master key is never used for model calls.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import DataClass

from z_server.db.models import Setting
from z_server.llm.inference import routing_config
from z_server.llm.litellm import LlmKeyError
from z_server.llm.routing import allowed_aliases
from z_server.state import AppState

KEY_SETTING = "platform_llm_key"
KEY_AAD = "llm-key:platform"
KEY_DAYS = 30
JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class ChatError(RuntimeError):
    pass


@dataclass
class Reply:
    text: str
    model: str
    tokens_in: int
    tokens_out: int


async def platform_key(db: AsyncSession, state: AppState) -> str:
    row = await db.get(Setting, KEY_SETTING, with_for_update=True)
    now = datetime.now(UTC)
    if row is not None and row.value.get("sealed"):
        expires = datetime.fromisoformat(row.value["expires_at"])
        if expires - now > timedelta(days=1):
            return state.sealer.open(bytes.fromhex(row.value["sealed"]), KEY_AAD)
    models = allowed_aliases(await routing_config(db, state.config), DataClass.C3)  # internal only
    if not models:
        raise ChatError("no internal model alias is configured")
    try:
        key = await state.llm_keys.create(
            alias="z-platform", models=models, metadata={"purpose": "platform"}, duration_s=KEY_DAYS * 86400
        )
    except LlmKeyError as exc:
        raise ChatError(f"the model gateway could not create the platform key: {exc}") from exc
    value = {
        "sealed": state.sealer.seal(key.key, KEY_AAD).hex(),
        "expires_at": (now + timedelta(days=KEY_DAYS)).isoformat(),
        "models": models,
    }
    if row is None:
        db.add(Setting(key=KEY_SETTING, value=value))
    else:
        row.value = value
    await db.flush()
    return key.key


async def complete(
    state: AppState,
    key: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 2000,
    json_mode: bool = False,
    timeout_s: float = 90,
) -> Reply:
    body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        resp = await state.http.post(
            f"{state.settings.litellm_url.rstrip('/')}/v1/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout_s,
        )
    except httpx.HTTPError as exc:
        raise ChatError(f"the model gateway is unreachable ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise ChatError(f"the model gateway answered {resp.status_code}")
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    usage = data.get("usage") or {}
    return Reply(
        str((choice.get("message") or {}).get("content") or ""),
        model,
        int(usage.get("prompt_tokens", 0)),
        int(usage.get("completion_tokens", 0)),
    )


def parse_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError:
        match = JSON_BLOCK.search(text)
        if not match:
            raise ChatError("the model did not return JSON") from None
        try:
            value = json.loads(match.group(0))
        except ValueError as exc:
            raise ChatError("the model returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise ChatError("the model returned JSON that is not an object")
    return value
