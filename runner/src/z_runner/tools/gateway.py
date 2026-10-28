"""Gateway operations: the job's read operations, served by the Z tool gateway over MCP (docs/05 §2).

The runner holds no connector credentials: it calls the gateway MCP endpoint (`spec.gateway_url`, streamable
HTTP) with `Authorization: Bearer <session token>`. The gateway checks the allowlist, injects credentials,
redacts and judge-scans the response. Model-facing tool names are `GatewayTool.name` (operation id with `.`
→ `__`), which are also the MCP tool names on the gateway.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import anyio
import httpx
import mcp.types as mcp_types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from z_common.models.api_internal import GatewayTool

from z_runner.tools.base import ToolContext, ToolResult, ZTool, untrusted

DEFAULT_TIMEOUT_S = 120.0


class GatewayError(Exception):
    """The gateway could not be reached or refused the session."""


class GatewayClient:
    """Minimal MCP client: one stateless session per call (the gateway is stateless per job)."""

    def __init__(self, url: str, token: Callable[[], str]) -> None:
        self.url = url
        self._token = token

    async def _call(self, name: str, arguments: dict[str, Any], timeout_s: float) -> mcp_types.CallToolResult:
        headers = {"Authorization": f"Bearer {self._token()}", "User-Agent": "z-runner"}
        timeout = httpx.Timeout(timeout_s, connect=10.0)
        with anyio.fail_after(timeout_s + 15):
            async with (
                httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False) as http,
                streamable_http_client(self.url, http_client=http) as (read, write, _session_id),
                ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout_s)) as session,
            ):
                await session.initialize()
                return await session.call_tool(name, arguments, read_timeout_seconds=timedelta(seconds=timeout_s))

    def call(
        self, name: str, arguments: dict[str, Any], timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> mcp_types.CallToolResult:
        try:
            return anyio.run(self._call, name, arguments, timeout_s)
        except (httpx.HTTPError, OSError, TimeoutError, McpError) as exc:
            raise GatewayError(f"{type(exc).__name__}: {exc}") from exc
        except BaseExceptionGroup as group:  # anyio task groups wrap transport errors
            first = _first_leaf(group)
            raise GatewayError(f"{type(first).__name__}: {first}") from group


def _first_leaf(group: BaseExceptionGroup[BaseException]) -> BaseException:
    exc: BaseException = group
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def result_text(result: mcp_types.CallToolResult) -> str:
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, mcp_types.TextContent):
            parts.append(block.text)
        elif isinstance(block, mcp_types.ImageContent):
            parts.append(f"[image {block.mimeType}, {len(block.data) * 3 // 4} bytes]")
        elif isinstance(block, mcp_types.EmbeddedResource | mcp_types.ResourceLink):
            parts.append(f"[resource {getattr(block, 'uri', '')}]")
    if not parts and result.structuredContent is not None:
        parts.append(json.dumps(result.structuredContent, ensure_ascii=False))
    return "\n".join(parts)


class GatewayOperationTool(ZTool):
    kind = "gateway"
    read_only = True

    def __init__(self, tool: GatewayTool, client: GatewayClient) -> None:
        self.name = tool.name
        self.operation_id = tool.operation
        self.description = tool.description
        self.input_schema = tool.input_schema
        self._client = client

    def operation(self, args: dict[str, Any]) -> str | None:
        return self.operation_id

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        timeout = max(5.0, min(DEFAULT_TIMEOUT_S, ctx.remaining_s() - 5.0))
        try:
            result = self._client.call(self.name, args, timeout)
        except GatewayError as exc:
            return ToolResult(
                text=f"The tool gateway could not complete {self.operation_id}: {exc}",
                ok=False,
                summary="gateway error",
            )
        text = ctx.redactor.text(result_text(result))
        ctx.progress.append(f"gateway:{self.operation_id}")
        summary = text.strip().splitlines()[0][:300] if text.strip() else "(empty result)"
        if result.isError:
            return ToolResult(text=untrusted(f"gateway:{self.operation_id}", text), ok=False, summary=summary)
        return ToolResult(text=untrusted(f"gateway:{self.operation_id}", text), summary=summary)
