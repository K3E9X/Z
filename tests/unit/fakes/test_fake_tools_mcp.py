from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tests.fakes.fake_tools import FakeTools

ROOT = Path(__file__).resolve().parents[3]
CONNECTORS = {
    "gitlab": ROOT / "packs/dev/connectors/gitlab.yaml",
    "confluence": ROOT / "packs/archi-isp/connectors/confluence.yaml",
}
# The Jira pack connector uses the Jira Cloud REST API v3; the fake also serves an MCP flavour for MCP connectors.
JIRA_MCP_TOOLS = {
    "jira_search",
    "jira_get_issue",
    "jira_create_issue",
    "jira_update_issue",
    "jira_add_comment",
    "jira_delete_issue",
}


@asynccontextmanager
async def client(tools: FakeTools, tool: str, kind: str) -> AsyncIterator[ClientSession]:
    http = httpx.AsyncClient(headers={"Authorization": f"Bearer {tools.token(tool, kind)}"})
    async with (
        http,
        streamable_http_client(tools.mcp_url(tool), http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


@pytest.mark.parametrize("tool", [*sorted(CONNECTORS), "jira"])
async def test_tools_match_operation_map(tools: FakeTools, tool: str) -> None:
    expected = JIRA_MCP_TOOLS if tool == "jira" else set(yaml.safe_load(CONNECTORS[tool].read_text())["operation_map"])
    async with client(tools, tool, "read") as c:
        listed = (await c.list_tools()).tools
    assert {t.name for t in listed} == expected
    for t in listed:
        assert t.inputSchema["type"] == "object"
        assert t.annotations is not None and t.annotations.readOnlyHint is not None
    destructive = {t.name for t in listed if t.annotations and t.annotations.destructiveHint}
    assert destructive <= {"jira_delete_issue", "merge_merge_request", "delete_branch"}


def test_mcp_requires_token(tools: FakeTools) -> None:
    assert httpx.post(tools.mcp_url("jira"), json={}).status_code == 401


async def test_jira_mcp_read_and_write_tokens(tools: FakeTools) -> None:
    tools.reset()
    async with client(tools, "jira", "read") as c:
        found = await c.call_tool("jira_search", {"jql": "key = SECOPS-4791"})
        assert not found.isError and found.structuredContent["total"] == 1
        issue = await c.call_tool("jira_get_issue", {"issue_key": "SECOPS-4791"})
        assert json.loads(issue.content[0].text)["key"] == "SECOPS-4791"
        denied = await c.call_tool("jira_create_issue", {"project_key": "SECOPS", "summary": "x"})
        assert denied.isError and "403" in denied.content[0].text
        invalid = await c.call_tool("jira_get_issue", {})
        assert invalid.isError
    async with client(tools, "jira", "write") as c:
        created = await c.call_tool("jira_create_issue", {"project_key": "SECOPS", "summary": "Upgrade log4j"})
        assert not created.isError and created.structuredContent["key"].startswith("SECOPS-")
        comment = await c.call_tool("jira_add_comment", {"issue_key": "SECOPS-4791", "comment": "Same finding"})
        assert not comment.isError
    writes = tools.writes()
    assert [w["type"] for w in writes] == ["jira.issues.create", "jira.comments.create"]
    assert all(w["token_kind"] == "write" for w in writes)
    assert any(d["what"] == "mcp jira_create_issue" for d in tools.state()["denied"])


async def test_gitlab_and_confluence_mcp(tools: FakeTools) -> None:
    tools.reset()
    async with client(tools, "gitlab", "read") as c:
        projects = await c.call_tool("list_projects", {"group": "infra"})
        assert projects.structuredContent["total"] == 4
        pom = await c.call_tool("get_file_contents", {"project_id": "infra/payments-api", "file_path": "pom.xml"})
        assert "2.14.1" in pom.structuredContent["content"]
        tree = await c.call_tool("get_file_contents", {"project_id": "101", "file_path": "src"})
        assert tree.structuredContent["type"] == "tree"
        diffs = await c.call_tool("get_merge_request_diffs", {"project_id": "101", "merge_request_iid": 1})
        assert diffs.structuredContent["diffs"][0]["new_path"] == "README.md"
        merge = await c.call_tool("merge_merge_request", {"project_id": "101", "merge_request_iid": 1})
        assert merge.isError
    async with client(tools, "confluence", "read") as c:
        hits = await c.call_tool("confluence_search", {"query": "flow matrix"})
        assert hits.structuredContent["results"][0]["id"] == "10002"
        page = await c.call_tool("confluence_get_page", {"page_id": "10001"})
        assert page.structuredContent["title"] == "SSO portal architecture"
        create = await c.call_tool("confluence_create_page", {"space_key": "SEC", "title": "t", "content": "c"})
        assert create.isError
    assert tools.writes() == []
