"""`z mcp serve`: the stdio MCP server calls the REST API with the profile's token (z_common.mcp_tools)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from mcp.shared.memory import create_connected_server_and_client_session
from z_cli.client import Api
from z_cli.commands.mcp import build_server


def test_tools_call_the_api_as_the_signed_in_user() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST" and request.url.path == "/api/v1/missions":
            body = json.loads(request.content)
            assert body == {"prompt": "triage the new findings"}
            return httpx.Response(201, json={"id": "mis_1", "title": "Triage", "status": "queued", "prompt": "x"})
        if request.url.path == "/api/v1/missions/mis_1/jobs":
            return httpx.Response(200, json={"items": [{"id": "job_1", "status": "running", "input": {}}]})
        if request.url.path == "/api/v1/missions/mis_1":
            return httpx.Response(200, json={"id": "mis_1", "status": "running"})
        return httpx.Response(
            403, json={"type": "urn:z:problem:forbidden", "title": "Forbidden", "detail": "missing permission"}
        )

    api = Api("https://z.example.internal", "zt_secret", transport=httpx.MockTransport(handler))
    results: dict[str, Any] = {}

    async def go() -> None:
        async with create_connected_server_and_client_session(build_server(api)) as client:
            results["tools"] = [t.name for t in (await client.list_tools()).tools]
            results["start"] = await client.call_tool("start_mission", {"prompt": "triage the new findings"})
            results["get"] = await client.call_tool("get_mission", {"id": "mis_1"})
            results["bad"] = await client.call_tool("get_mission", {"id": "../../admin/users"})
            results["denied"] = await client.call_tool("list_review", {})

    asyncio.run(go())
    assert results["tools"] == ["start_mission", "get_mission", "list_playbooks", "list_review"]
    started = json.loads(results["start"].content[0].text)
    assert started["url"] == "https://z.example.internal/missions/mis_1" and "prompt" not in started
    got = json.loads(results["get"].content[0].text)
    assert got["job_list"] == [{"id": "job_1", "status": "running"}]
    assert results["bad"].isError and "is an id" in results["bad"].content[0].text
    assert results["denied"].isError and results["denied"].content[0].text == "Z refused (403): missing permission"
    assert all(r.headers["authorization"] == "Bearer zt_secret" for r in seen)
    assert all(r.headers["user-agent"].startswith("z-mcp (z-cli/") for r in seen)
    assert not any("admin" in r.url.path for r in seen)
