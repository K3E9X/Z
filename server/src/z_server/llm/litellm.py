"""LiteLLM proxy administration: one virtual key per mission with an explicit `models` allowlist (docs/06 §4).

Endpoints used (LiteLLM proxy, verified against v1.105.0): POST /key/generate, POST /key/update,
POST /key/delete, GET /key/info. Authenticated with the master key, which only z-server holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class LlmKeyError(RuntimeError):
    pass


@dataclass
class VirtualKey:
    key: str
    alias: str
    models: list[str]


class KeyAdmin(Protocol):
    async def create(self, alias: str, models: list[str], metadata: dict[str, Any], duration_s: int) -> VirtualKey: ...

    async def set_models(self, key: str, models: list[str]) -> None: ...

    async def delete(self, key: str) -> None: ...


class LiteLLMKeyAdmin:
    def __init__(self, base_url: str, master_key: str, http: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self.master_key = master_key
        self.http = http

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self.master_key:
            raise LlmKeyError("LITELLM_MASTER_KEY is not configured")
        try:
            resp = await self.http.post(
                f"{self.base_url}{path}", json=body, headers={"Authorization": f"Bearer {self.master_key}"}, timeout=20
            )
        except httpx.HTTPError as exc:
            raise LlmKeyError(f"LiteLLM unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise LlmKeyError(f"LiteLLM {path} refused ({resp.status_code}): {resp.text[:200]}")
        data: dict[str, Any] = resp.json()
        return data

    async def create(self, alias: str, models: list[str], metadata: dict[str, Any], duration_s: int) -> VirtualKey:
        data = await self._post(
            "/key/generate", {"key_alias": alias, "models": models, "metadata": metadata, "duration": f"{duration_s}s"}
        )
        return VirtualKey(key=str(data["key"]), alias=alias, models=list(models))

    async def set_models(self, key: str, models: list[str]) -> None:
        await self._post("/key/update", {"key": key, "models": models})

    async def delete(self, key: str) -> None:
        await self._post("/key/delete", {"keys": [key]})
