"""Test double of the LiteLLM key administration (records calls, never contacts a proxy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from z_server.llm.litellm import VirtualKey


@dataclass
class RecordingKeyAdmin:
    keys: dict[str, VirtualKey] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)

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
