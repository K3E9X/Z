"""`/gateway/mcp`: the per-job MCP endpoint (streamable HTTP, stateless, JSON responses).

Authentication is the job's session token (`Authorization: Bearer`); the tools listed are exactly the job's
allowlist; each call goes through `Gateway.call`. Verdicts travel in the result `_meta`
(`io.z/judge`, `io.z/artifact`, `io.z/truncated`) so the runner can label the tool result.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from z_server.gateway.core import Gateway, GatewayRefused

PATH = "/gateway/mcp"


def _token(headers: Any) -> str:
    auth = (headers.get("authorization") if headers is not None else "") or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


class GatewayEndpoint:
    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        self.server: Server[Any, Any] = Server("z-gateway", version="1.0.0")
        self.server.list_tools()(self._list_tools)  # type: ignore[no-untyped-call]  # SDK decorator is untyped
        self.server.call_tool(validate_input=False)(self._call_tool)
        self._manager: StreamableHTTPSessionManager | None = None

    def _request_token(self) -> str:
        request = self.server.request_context.request
        return _token(getattr(request, "headers", None))

    async def _list_tools(self) -> list[types.Tool]:
        tools = await self.gateway.tools(self._request_token())
        return [
            types.Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
                annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True),
            )
            for t in tools
        ]

    async def _call_tool(self, name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        try:
            outcome = await self.gateway.call(self._request_token(), name, arguments)
        except GatewayRefused as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=exc.message)], isError=True)
        meta: dict[str, Any] = {"io.z/judge": outcome.judge.value, "io.z/truncated": outcome.truncated}
        if outcome.artifact_id:
            meta["io.z/artifact"] = outcome.artifact_id
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=outcome.text)],
            isError=not outcome.ok,
            _meta=meta,
        )

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        self._manager = StreamableHTTPSessionManager(
            app=self.server,
            json_response=True,
            stateless=True,
            # Host/Origin checks: the endpoint is only reachable on the runners network and needs a job token.
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        async with self._manager.run():
            yield
        self._manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        token = _token(headers)
        try:
            if not token:
                raise GatewayRefused("unauthenticated", "job token required")
            await self.gateway.job_for(token)
        except GatewayRefused as exc:
            await JSONResponse(
                {"error": exc.message}, status_code=401, headers={"WWW-Authenticate": 'Bearer realm="z-gateway"'}
            )(scope, receive, send)
            return
        if self._manager is None:
            await JSONResponse({"error": "gateway not started"}, status_code=503)(scope, receive, send)
            return
        await self._manager.asgi_app(scope, receive, send)
