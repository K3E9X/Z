"""LiteLLM proxy administration: one virtual key per mission with an explicit `models` allowlist (docs/06 §4).

Endpoints used (LiteLLM proxy, verified against v1.105.0): POST /key/generate, POST /key/update,
POST /key/delete, GET /key/info; and for the inferX key (ADR 0027) GET /credentials/by_name/{name}
(the live in-memory credential, values masked), PATCH /credentials/{name} (DB and memory; 404 when the
credential is not in LiteLLM's DB yet), POST /credentials (DB and memory) and GET /health?model= (one real
call per deployment of the alias). Authenticated with the master key, which only z-server holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

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

    async def set_models(self, key: str, models: list[str], metadata: dict[str, Any] | None = None) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def credential(self, name: str) -> dict[str, Any] | None:
        """The credential LiteLLM serves now (values masked), or None when it has none of that name."""

    async def set_credential(self, name: str, values: dict[str, str], info: dict[str, Any]) -> None:
        """Create or replace a provider credential (LiteLLM encrypts it in its database)."""

    async def check_model(self, alias: str) -> tuple[bool, str]:
        """(ok, detail) of one real call to the deployments of `alias`."""


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

    async def set_models(self, key: str, models: list[str], metadata: dict[str, Any] | None = None) -> None:
        """Restrict the key's models; `metadata` (the whole dict) replaces the key's metadata when given."""
        body: dict[str, Any] = {"key": key, "models": models}
        if metadata is not None:
            body["metadata"] = metadata
        await self._post("/key/update", body)

    async def delete(self, key: str) -> None:
        await self._post("/key/delete", {"keys": [key]})

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None, **kw: Any) -> httpx.Response:
        if not self.master_key:
            raise LlmKeyError("LITELLM_MASTER_KEY is not configured")
        try:
            return await self.http.request(
                method,
                f"{self.base_url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self.master_key}"},
                timeout=kw.pop("timeout", 20),
                **kw,
            )
        except httpx.HTTPError as exc:
            raise LlmKeyError(f"LiteLLM unreachable: {type(exc).__name__}") from exc

    async def credential(self, name: str) -> dict[str, Any] | None:
        resp = await self._request("GET", f"/credentials/by_name/{quote(name, safe='')}")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise LlmKeyError(f"LiteLLM /credentials refused ({resp.status_code})")
        data: dict[str, Any] = resp.json()
        return data

    async def set_credential(self, name: str, values: dict[str, str], info: dict[str, Any]) -> None:
        # never echo LiteLLM's answer: it could quote the request
        body = {"credential_name": name, "credential_values": values, "credential_info": info}
        resp = await self._request("PATCH", f"/credentials/{quote(name, safe='')}", body)
        if resp.status_code == 404:  # only in the config file (or nowhere): create it in LiteLLM's database
            resp = await self._request("POST", "/credentials", body)
        if resp.status_code >= 400:
            raise LlmKeyError(f"LiteLLM refused the credential ({resp.status_code})")

    async def check_model(self, alias: str) -> tuple[bool, str]:
        resp = await self._request("GET", "/health", params={"model": alias}, timeout=60)
        if resp.status_code >= 400:
            return False, f"LiteLLM /health answered {resp.status_code}"
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if int(data.get("healthy_count") or 0) > 0:
            return True, f"{alias} answered"
        errors = [str(e.get("error") or "") for e in data.get("unhealthy_endpoints") or [] if isinstance(e, dict)]
        first = errors[0].strip().splitlines()[0] if errors and errors[0].strip() else ""
        return False, first or f"{alias} did not answer"  # the first line: LiteLLM appends a stack trace
