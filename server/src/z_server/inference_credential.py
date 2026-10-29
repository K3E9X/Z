"""The inferX key the LiteLLM gateway uses (ADR 0027), set from Z instead of the deployment's environment.

The key goes to LiteLLM only: its credentials API keeps it encrypted in LiteLLM's database and serves it to the
deployments that name the `inferx` credential, from the next call on (no restart). Z never stores, returns, logs or
audits the key: it keeps who set it and when, and the result of the last check (one real call through the
gateway), and shows the key's fingerprint (the first 12 hex of its SHA-256) so two keys can be told apart.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from z_common.redaction import Redactor

from z_server.db.models import Setting
from z_server.errors import Problem
from z_server.llm.litellm import LlmKeyError
from z_server.state import AppState

CREDENTIAL = "inferx"  # litellm_credential_name of the inferX deployments (config/litellm.yaml)
SETTING = "inference_credential"
MANAGED_BY = "z"


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def check_alias(state: AppState) -> str:
    """The alias the check calls: a chat model of the internal cluster (z-fast when configured)."""
    aliases = [str(a) for a in (state.config.get("inference") or {}).get("aliases", []) if str(a) != "z-embed"]
    return "z-fast" if "z-fast" in aliases else (aliases[0] if aliases else "z-fast")


async def check(state: AppState, key: str | None = None) -> dict[str, Any]:
    alias = check_alias(state)
    try:
        ok, detail = await state.llm_keys.check_model(alias)
    except LlmKeyError as exc:
        ok, detail = False, str(exc)
    # the gateway's error text can quote the request: never let the key through
    return {
        "ok": ok,
        "model": alias,
        "detail": Redactor([key] if key else []).text(detail)[:300],
        "checked_at": datetime.now(UTC).isoformat(),
    }


async def status(db: AsyncSession, state: AppState) -> dict[str, Any]:
    row = await db.get(Setting, SETTING)
    meta: dict[str, Any] = dict(row.value) if row else {}
    try:
        live = await state.llm_keys.credential(CREDENTIAL)
    except LlmKeyError:
        return {"source": "unknown", **{k: meta.get(k) for k in ("fingerprint", "updated_at", "updated_by", "check")}}
    if live is None or not (live.get("credential_values") or {}).get("api_key"):
        return {"source": "none", "check": meta.get("check")}
    info = live.get("credential_info") or {}
    if info.get("managed_by") == MANAGED_BY:
        return {
            "source": "ui",
            "fingerprint": info.get("fingerprint"),
            "updated_at": meta.get("updated_at"),
            "updated_by": meta.get("updated_by"),
            "check": meta.get("check"),
        }
    return {"source": "env", "check": meta.get("check")}


def internal_aliases(state: AppState) -> list[str]:
    return [str(a) for a in (state.config.get("inference") or {}).get("aliases", []) if not str(a).startswith("ext-")]


async def replace(db: AsyncSession, state: AppState, username: str, key: str, test: bool) -> dict[str, Any]:
    fp = fingerprint(key)
    try:
        # a deployment that still carries its own api_key ignores the credential: refuse rather than show a key
        # as in use while the gateway keeps calling inferX with the old one
        stale = await state.llm_keys.aliases_without_credential(internal_aliases(state))
    except LlmKeyError as exc:
        raise Problem("unavailable", f"the model gateway does not answer: {exc}") from exc
    if stale:
        raise Problem(
            "conflict",
            f"the gateway's models {', '.join(stale)} carry their own api_key: set litellm_credential_name: inferx "
            "on them in config/litellm.yaml (see config/litellm.example.yaml, ADR 0027) and restart LiteLLM",
        )
    try:
        await state.llm_keys.set_credential(CREDENTIAL, {"api_key": key}, {"managed_by": MANAGED_BY, "fingerprint": fp})
    except LlmKeyError as exc:
        raise Problem("unavailable", f"the model gateway did not take the key: {exc}") from exc
    meta: dict[str, Any] = {"fingerprint": fp, "updated_at": datetime.now(UTC).isoformat(), "updated_by": username}
    if test:
        meta["check"] = await check(state, key)
    row = await db.get(Setting, SETTING, with_for_update=True)
    if row is None:
        db.add(Setting(key=SETTING, value=meta))
    else:
        row.value = meta
    await db.flush()
    return {"source": "ui", **meta}


async def retest(db: AsyncSession, state: AppState) -> dict[str, Any]:
    row = await db.get(Setting, SETTING, with_for_update=True)
    result = await check(state)
    value = {**(row.value if row else {}), "check": result}
    if row is None:
        db.add(Setting(key=SETTING, value=value))
    else:
        row.value = value
    await db.flush()
    return await status(db, state)
