"""Test double of the LiteLLM key administration (records calls, never contacts a proxy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from z_server.llm.litellm import VirtualKey


@dataclass
class RecordingKeyAdmin:
    keys: dict[str, VirtualKey] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)
    credentials: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> {credential_values, credential_info}
    healthy: bool = True
    reachable: bool = True
    without_credential: list[str] = field(default_factory=list)  # aliases still carrying their own api_key

    async def create(self, alias: str, models: list[str], metadata: dict[str, Any], duration_s: int) -> VirtualKey:
        key = VirtualKey(key=f"sk-test-{len(self.keys) + 1:04d}-{alias}", alias=alias, models=list(models))
        self.keys[key.key] = key
        self.calls.append(("create", {"alias": alias, "models": models, "metadata": metadata}))
        return key

    async def set_models(self, key: str, models: list[str], metadata: dict[str, Any] | None = None) -> None:
        self.keys[key].models = list(models)
        self.calls.append(("set_models", {"key": key, "models": models, "metadata": metadata}))

    async def delete(self, key: str) -> None:
        self.keys.pop(key, None)
        self.calls.append(("delete", {"key": key}))

    async def credential(self, name: str) -> dict[str, Any] | None:
        if not self.reachable:
            from z_server.llm.litellm import LlmKeyError

            raise LlmKeyError("LiteLLM unreachable: ConnectError")
        found = self.credentials.get(name)
        if found is None:
            return None
        masked = {k: "****" + v[-4:] for k, v in found["credential_values"].items()}  # as LiteLLM masks
        return {"credential_name": name, "credential_values": masked, "credential_info": found["credential_info"]}

    async def set_credential(self, name: str, values: dict[str, str], info: dict[str, Any]) -> None:
        self.credentials[name] = {"credential_values": dict(values), "credential_info": dict(info)}
        self.calls.append(("set_credential", {"name": name, "info": info}))  # never the values

    async def check_model(self, alias: str) -> tuple[bool, str]:
        self.calls.append(("check_model", {"alias": alias}))
        key = (self.credentials.get("inferx") or {}).get("credential_values", {}).get("api_key", "")
        return (True, f"{alias} answered") if self.healthy else (False, f"401 key_inert for {key}")

    async def aliases_without_credential(self, aliases: list[str]) -> list[str]:
        return [a for a in aliases if a in self.without_credential]
