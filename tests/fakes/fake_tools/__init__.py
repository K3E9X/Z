"""Fake DefectDojo, CMDB, Logos, Jira, GitLab and Confluence in one app. See tests/fakes/README.md."""

from __future__ import annotations

from typing import Any

from .._server import FakeServer
from .app import create_app
from .seed import default_seed
from .state import TOOLS, default_tokens

__all__ = ["TOOLS", "FakeTools", "create_app", "default_seed", "default_tokens"]


class FakeTools(FakeServer):
    """In-process fake tools. `url_for("jira")` gives the tool base URL (e.g. http://127.0.0.1:PORT/jira)."""

    def __init__(
        self, seed: dict[str, Any] | None = None, tokens: dict[str, dict[str, str]] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(create_app(seed, tokens), **kwargs)
        self.tokens: dict[str, dict[str, str]] = self.app.state.store.tokens

    def url_for(self, tool: str) -> str:
        return f"{self.url}/{tool}"

    def mcp_url(self, tool: str) -> str:
        return f"{self.url}/{tool}/mcp"

    def token(self, tool: str, kind: str = "read") -> str:
        return self.tokens[tool][kind]

    def reset(self) -> None:
        with self.client() as c:
            c.post("/_fake/reset").raise_for_status()

    def seed(self, data: dict[str, Any]) -> None:
        with self.client() as c:
            c.post("/_fake/seed", json=data).raise_for_status()

    def state(self) -> dict[str, Any]:
        with self.client() as c:
            r = c.get("/_fake/state")
            r.raise_for_status()
            result: dict[str, Any] = r.json()
            return result

    def writes(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Recorded writes, optionally filtered by type (e.g. "jira.issues.create")."""
        return [w for w in self.state()["writes"] if kind is None or w["type"] == kind]
