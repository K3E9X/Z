"""Errors shared by the REST routes and MCP tools of the fake tools."""

from __future__ import annotations

from typing import Any

from .._mcp import ToolError


class FakeToolError(ToolError):
    """Mapped to an HTTP status by the REST routes, to an MCP error result by the MCP tools."""

    status = 400

    def __init__(self, message: str, body: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.body = body


class NotFound(FakeToolError):
    status = 404


class BadRequest(FakeToolError):
    status = 400


class Conflict(FakeToolError):
    status = 409
