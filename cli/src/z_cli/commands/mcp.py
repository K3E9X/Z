"""`z mcp serve`: Z's tools for a local MCP client over stdio (docs/10), with the signed-in profile's token.

The tools are those of `/mcp` on the server (z_common.mcp_tools); each one is a REST call with your own token, so
what a client can do is exactly what you can do in Z. Nothing is printed on stdout except the MCP protocol.
"""

from __future__ import annotations

import asyncio
from typing import Any

import mcp.types as types
import typer
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from z_common import mcp_tools

from z_cli import __version__, state
from z_cli.client import USER_AGENT, Api
from z_cli.commands._common import command

mcp_app = typer.Typer(help="Z as an MCP server for your MCP clients.", no_args_is_help=True)


def build_server(api: Api) -> Server[Any, Any]:
    server: Server[Any, Any] = Server("z", version=__version__)
    headers = {"User-Agent": f"z-mcp ({USER_AGENT})"}

    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]  # SDK decorator is untyped
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=t.name,
                title=t.title,
                description=t.description,
                inputSchema=t.input_schema,
                annotations=types.ToolAnnotations(
                    title=t.title, readOnlyHint=t.read_only, destructiveHint=False, openWorldHint=False
                ),
            )
            for t in mcp_tools.TOOLS
        ]

    def _request(method: str, path: str, params: dict[str, Any] | None, body: Any) -> tuple[int, Any]:
        resp = api.http.request(method, path, params=params, json=body, headers=headers)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, None

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        def error(text: str) -> types.CallToolResult:
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)

        try:
            call = mcp_tools.call_for(name, arguments)
        except mcp_tools.ToolInputError as exc:
            return error(str(exc))
        try:
            status, body = await asyncio.to_thread(_request, call.method, call.path, call.params, call.body)
            if status >= 400:
                return error(mcp_tools.problem_text(status, body))
            extra = None
            if call.then:
                more_status, more = await asyncio.to_thread(_request, "GET", call.then, {"limit": 100}, None)
                extra = more if more_status < 400 else None
        except Exception as exc:  # noqa: BLE001 - network errors become a tool error, never a crash of the server
            return error(f"Z is unreachable at {api.url}: {type(exc).__name__}")
        text = mcp_tools.result_text(name, body, extra, api.web_url)
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=False)

    return server


async def _serve(server: Server[Any, Any]) -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


@command(mcp_app, "serve")
def serve() -> None:
    """Serve start_mission, get_mission, list_playbooks and list_review over stdio, as you.

    Example client configuration: {"command": "z", "args": ["mcp", "serve"]}. Approvals stay in Z Review.
    """
    api = state.get().api()  # not signed in: a readable error before the protocol starts
    asyncio.run(_serve(build_server(api)))
