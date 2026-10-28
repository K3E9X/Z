"""A minimal MCP server (official `mcp` SDK, low-level API) over streamable HTTP with bearer-token checks.

Every tool declares an access level. Any known token may list tools and call read tools; write and
destructive tools need the write token, otherwise the call returns an MCP error result (``isError``).
Requests without a known token get HTTP 401 before reaching the MCP layer.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

import jsonschema
import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

Access = Literal["read", "write", "destructive"]
TokenKind = Literal["read", "write"]


class ToolError(Exception):
    """Raised by a tool handler to return an MCP error result."""


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any], TokenKind], Any]
    access: Access = "read"

    def to_mcp(self) -> types.Tool:
        annotations = types.ToolAnnotations(
            read_only_hint=self.access == "read",
            destructive_hint=self.access == "destructive" if self.access != "read" else None,
            idempotent_hint=True if self.access == "read" else None,
        )
        return types.Tool(
            name=self.name, description=self.description, input_schema=self.input_schema, annotations=annotations
        )


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(text=message)], is_error=True)


class McpEndpoint:
    """ASGI endpoint serving one MCP server. Enter `run()` in the host app's lifespan."""

    def __init__(
        self,
        name: str,
        tools: list[McpTool],
        authenticate: Callable[[str | None], TokenKind | None],
        on_denied: Callable[[str, str], None] | None = None,
    ) -> None:
        self.name = name
        self.tools = {t.name: t for t in tools}
        self.authenticate = authenticate
        self.on_denied = on_denied
        self._manager: StreamableHTTPSessionManager | None = None
        self.server: Server[Any] = Server(
            name, version="0.1.0", on_list_tools=self._list_tools, on_call_tool=self._call_tool
        )

    async def _list_tools(
        self, ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[t.to_mcp() for t in self.tools.values()])

    async def _call_tool(self, ctx: ServerRequestContext[Any], params: types.CallToolRequestParams) -> Any:
        tool = self.tools.get(params.name)
        if tool is None:
            return _error(f"unknown tool: {params.name}")
        headers = getattr(ctx.request, "headers", None)
        kind = self.authenticate(headers.get("authorization") if headers is not None else None)
        if kind is None:
            return _error("401 Unauthorized: missing or unknown token")
        if tool.access != "read" and kind != "write":
            if self.on_denied is not None:
                self.on_denied(tool.name, "read token used for a write tool")
            return _error(f"403 Forbidden: {tool.name} needs the write token")
        args = dict(params.arguments or {})
        try:
            jsonschema.validate(args, tool.input_schema)
        except jsonschema.ValidationError as exc:
            return _error(f"invalid arguments: {exc.message}")
        try:
            result = tool.handler(args, kind)
        except ToolError as exc:
            return _error(str(exc))
        structured = result if isinstance(result, dict) else {"result": result}
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(result, default=str))], structured_content=structured
        )

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        self._manager = StreamableHTTPSessionManager(
            app=self.server,
            json_response=True,
            stateless=True,
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        async with self._manager.run():
            yield
        self._manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if self.authenticate(headers.get("authorization")) is None:
            response = JSONResponse(
                {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": 'Bearer realm="fake"'}
            )
            await response(scope, receive, send)
            return
        if self._manager is None:
            await JSONResponse({"error": "MCP endpoint not started"}, status_code=503)(scope, receive, send)
            return
        await self._manager.asgi_app(scope, receive, send)
