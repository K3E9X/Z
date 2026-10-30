"""Client side of `mcp` connectors: the gateway calls the tool's MCP server (streamable HTTP or a stdio sidecar).

The upstream tool name is the `operation_map` key whose value is the local operation id. Credentials go in
the HTTP headers (http transport) or the sidecar environment (stdio, from `mcp.env` SecretRefs plus the auth
secret as `Z_TOOL_TOKEN`). One short session per call keeps the gateway stateless.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any

import anyio
import httpx
import mcp.types as mcp_types
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError

from z_server.gateway.http import InvokeError
from z_server.gateway.secrets import SecretResolver


def upstream_name(manifest: dict[str, Any], local_id: str) -> str:
    for tool, mapped in (manifest.get("operation_map") or {}).items():
        if mapped == local_id:
            return str(tool)
    raise InvokeError(f"operation {local_id} has no MCP tool in operation_map")


async def _stdio_params(
    manifest: dict[str, Any], resolver: SecretResolver, auth: dict[str, str]
) -> StdioServerParameters:
    server = manifest.get("mcp") or {}
    if not server.get("command"):
        raise InvokeError("stdio MCP connector without a command")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp"}  # noqa: S108 - sidecar scratch home
    for name, value in (server.get("env") or {}).items():
        env[name] = (
            await resolver.resolve(value)
            if isinstance(value, str)
            and ":" in value
            and value.split(":", 1)[0] in {"env", "file", "vault", "kubernetes"}
            else str(value)
        )
    if "Authorization" in auth:
        env["Z_TOOL_TOKEN"] = auth["Authorization"].split(" ", 1)[-1]
    return StdioServerParameters(
        command=str(server["command"]), args=[str(a) for a in server.get("args") or []], env=env
    )


async def _session_call(
    session: ClientSession, name: str, args: dict[str, Any], timeout_s: float
) -> mcp_types.CallToolResult:
    await session.initialize()
    return await session.call_tool(name, args, read_timeout_seconds=timedelta(seconds=timeout_s))


async def call(
    manifest: dict[str, Any],
    base: str | None,
    local_id: str,
    args: dict[str, Any],
    auth_headers: dict[str, str],
    resolver: SecretResolver,
    timeout_s: float = 30.0,
) -> mcp_types.CallToolResult:
    name = upstream_name(manifest, local_id)
    server = manifest.get("mcp") or {}
    try:
        with anyio.fail_after(timeout_s + 10):
            if server.get("transport") == "stdio":
                params = await _stdio_params(manifest, resolver, auth_headers)
                async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                    return await _session_call(session, name, args, timeout_s)
            url = server.get("url") or base
            if not url:
                raise InvokeError("http MCP connector without a url")
            headers = {"User-Agent": "z-gateway", **auth_headers}
            async with (
                httpx.AsyncClient(
                    headers=headers, timeout=httpx.Timeout(timeout_s, connect=10), follow_redirects=False
                ) as http,
                streamable_http_client(str(url), http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                return await _session_call(session, name, args, timeout_s)
    except TimeoutError as exc:
        raise InvokeError(f"MCP call timed out after {timeout_s:.0f} s", retryable=True) from exc
    except (httpx.HTTPError, OSError, McpError) as exc:
        raise InvokeError(f"MCP server unreachable or refused: {type(exc).__name__}", retryable=True) from exc
    except BaseExceptionGroup as group:
        leaf: BaseException = group
        while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:
            leaf = leaf.exceptions[0]
        if isinstance(leaf, InvokeError):
            raise leaf from group
        status = getattr(getattr(leaf, "response", None), "status_code", None)
        raise InvokeError(f"MCP server error: {type(leaf).__name__}", status=status, retryable=True) from group


async def list_tools(
    manifest: dict[str, Any], base: str | None, auth_headers: dict[str, str], resolver: SecretResolver
) -> list[mcp_types.Tool]:
    """Discovery for import and test: every tool the server advertises (filtering happens in materialize)."""
    server = manifest.get("mcp") or {}
    try:
        with anyio.fail_after(40):
            if server.get("transport") == "stdio":
                params = await _stdio_params(manifest, resolver, auth_headers)
                async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                    await session.initialize()
                    return list((await session.list_tools()).tools)
            url = server.get("url") or base
            async with (
                httpx.AsyncClient(
                    headers={"User-Agent": "z-gateway", **auth_headers}, timeout=30, follow_redirects=False
                ) as http,
                streamable_http_client(str(url), http_client=http) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                return list((await session.list_tools()).tools)
    except BaseExceptionGroup as group:
        raise InvokeError(f"MCP discovery failed: {_describe(group)}") from group
    except (TimeoutError, httpx.HTTPError, OSError, McpError) as exc:
        raise InvokeError(f"MCP discovery failed: {_describe(exc)}") from exc


def _describe(exc: BaseException) -> str:
    leaf: BaseException = exc
    while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:
        leaf = leaf.exceptions[0]
    status = getattr(getattr(leaf, "response", None), "status_code", None)
    return f"{type(leaf).__name__}" + (f" (HTTP {status})" if status else "")


def result_payload(result: mcp_types.CallToolResult) -> Any:
    if result.structuredContent is not None:
        return result.structuredContent
    texts = [b.text for b in result.content if isinstance(b, mcp_types.TextContent)]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except ValueError:
        return joined
