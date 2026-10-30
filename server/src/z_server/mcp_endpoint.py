"""`/mcp`: Z as an MCP server for people's own MCP clients (docs/03 §5), streamable HTTP, stateless, JSON.

Authentication is a personal API token (`zt_…`) or a CLI session token as `Authorization: Bearer`; cookies are
never accepted, so a web page cannot ride a signed-in browser. Each tool is the matching REST operation, called
in-process with the same bearer token and the caller's address: route permissions, step-up, validation, audit
and rate limits apply unchanged (z_common.mcp_tools).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from z_common import mcp_tools

from z_server import __version__
from z_server.api.framework import API_PREFIX, authenticate
from z_server.errors import Problem
from z_server.state import AppState

PATH = "/mcp"
INTERNAL_BASE = "http://z-mcp.internal"


def _bearer(headers: Any) -> str:
    auth = (headers.get("authorization") if headers is not None else "") or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


class ZMcpEndpoint:
    def __init__(self) -> None:
        self.app: ASGIApp | None = None  # the FastAPI app, bound after creation
        self.state: AppState | None = None
        self.server: Server[Any, Any] = Server("z", version=__version__)
        self.server.list_tools()(self._list_tools)  # type: ignore[no-untyped-call]  # SDK decorator is untyped
        self.server.call_tool(validate_input=False)(self._call_tool)
        self._manager: StreamableHTTPSessionManager | None = None

    async def _list_tools(self) -> list[types.Tool]:
        return [
            types.Tool(
                name=t.name,
                title=t.title,
                description=t.description,
                inputSchema=t.input_schema,
                annotations=types.ToolAnnotations(
                    title=t.title,
                    readOnlyHint=t.read_only,
                    destructiveHint=False,
                    idempotentHint=t.read_only,
                    openWorldHint=False,
                ),
            )
            for t in mcp_tools.TOOLS
        ]

    def _error(self, text: str) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)

    async def _call_tool(self, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        request = self.server.request_context.request
        headers = getattr(request, "headers", None)
        client = getattr(request, "client", None)
        try:
            call = mcp_tools.call_for(name, arguments)
        except mcp_tools.ToolInputError as exc:
            return self._error(str(exc))
        if self.app is None or self.state is None:
            return self._error("Z is starting")
        forwarded = {"User-Agent": f"z-mcp ({(headers or {}).get('user-agent', 'unknown client')})"[:200]}
        token = _bearer(headers)
        if token:
            forwarded["Authorization"] = f"Bearer {token}"
        transport = httpx.ASGITransport(app=self.app, client=(client.host if client else "127.0.0.1", 0))
        async with httpx.AsyncClient(transport=transport, base_url=INTERNAL_BASE + API_PREFIX, timeout=60) as http:
            resp = await http.request(call.method, call.path, params=call.params, json=call.body, headers=forwarded)
            body = _json(resp)
            if resp.status_code >= 400:
                return self._error(mcp_tools.problem_text(resp.status_code, body))
            extra = None
            if call.then:
                more = await http.get(call.then, params={"limit": 100}, headers=forwarded)
                extra = _json(more) if more.status_code < 400 else None
        text = mcp_tools.result_text(name, body, extra, self.state.settings.public_url)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=False)

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        self._manager = StreamableHTTPSessionManager(
            app=self.server,
            json_response=True,
            stateless=True,
            # Bearer tokens only (no cookie): a rebound host name gives a page no credential to use.
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        async with self._manager.run():
            yield
        self._manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = self.state
        if state is None or self._manager is None:
            await JSONResponse({"error": "Z is starting"}, status_code=503)(scope, receive, send)
            return
        request = Request(scope)
        dev = state.settings.auth_mode == "dev" and not state.settings.production
        try:
            if not _bearer(request.headers) and not dev:
                raise Problem("unauthenticated", "an API token is required (Settings → API tokens)")
            async with state.sessions() as db, db.begin():
                # a bearer token is checked here; cookies are ignored: only the Authorization header is forwarded
                principal = await authenticate(_without_cookies(request), db, state)
            if principal is None:
                raise Problem("unauthenticated", "an API token is required (Settings → API tokens)")
        except Problem as exc:
            await JSONResponse(
                {"error": exc.detail}, status_code=401, headers={"WWW-Authenticate": 'Bearer realm="z"'}
            )(scope, receive, send)
            return
        await self._manager.asgi_app(scope, receive, send)


def _without_cookies(request: Request) -> Request:
    scope = dict(request.scope)
    scope["headers"] = [(k, v) for k, v in request.scope.get("headers", []) if k.lower() != b"cookie"]
    return Request(scope)


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None
