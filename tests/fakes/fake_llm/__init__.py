"""Scriptable OpenAI-compatible fake LLM. See tests/fakes/README.md."""

from __future__ import annotations

from typing import Any

from .._server import FakeServer
from .app import create_app

__all__ = ["FakeLLM", "create_app"]


class FakeLLM(FakeServer):
    """In-process fake LLM server with the control API as methods."""

    def __init__(self, rules: list[dict[str, Any]] | None = None, rules_file: str | None = None, **kwargs: Any) -> None:
        super().__init__(create_app(rules_file=rules_file, embed_dim=kwargs.pop("embed_dim", None)), **kwargs)
        self._initial_rules = rules

    @property
    def base_url(self) -> str:
        """OpenAI-style base URL (`.../v1`)."""
        return f"{self.url}/v1"

    def start(self, timeout: float = 10.0) -> FakeLLM:
        super().start(timeout)
        if self._initial_rules:
            self.add_rules(self._initial_rules)
        return self

    def add_rules(self, rules: dict[str, Any] | list[dict[str, Any]]) -> list[str]:
        with self.client() as c:
            r = c.post("/_fake/rules", json=rules)
            r.raise_for_status()
            ids: list[str] = r.json()["ids"]
            return ids

    def clear_rules(self) -> None:
        with self.client() as c:
            c.delete("/_fake/rules").raise_for_status()

    def requests(self) -> list[dict[str, Any]]:
        with self.client() as c:
            r = c.get("/_fake/requests")
            r.raise_for_status()
            data: list[dict[str, Any]] = r.json()
            return data

    def clear_requests(self) -> None:
        with self.client() as c:
            c.delete("/_fake/requests").raise_for_status()

    def reset(self) -> None:
        with self.client() as c:
            c.post("/_fake/reset").raise_for_status()
