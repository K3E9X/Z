from __future__ import annotations

import json
from pathlib import Path

import httpx
import httpx2
import pytest
import yaml
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from tests.fakes.fake_tools import FakeTools

ROOT = Path(__file__).resolve().parents[3]
CONNECTORS = {
    "jira": ROOT / "packs/ticketing/connectors/jira.yaml",
    "gitlab": ROOT / "packs/dev/connectors/gitlab.yaml",
    "confluence": ROOT / "packs/archi-isp/connectors/confluence.yaml",
}


def client(tools: FakeTools, tool: str, kind: str) -> Client:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {tools.token(tool, kind)}"})
    return Client(streamable_http_client(tools.mcp_url(tool), http_client=http))


@pytest.mark.parametrize("tool", sorted(CONNECTORS))
async def test_tools_match_operation_map(tools: FakeTools, tool: str) -> None:
    expected = set(yaml.safe_load(CONNECTORS[tool].read_text())["operation_map"])
    async with client(tools, tool, "read") as c:
        listed = (await c.list_tools()).tools
    assert {t.name for t in listed} == expected
    for t in listed:
        assert t.input_schema["type"] == "object"
        assert t.annotations is not None and t.annotations.read_only_hint is not None
    destructive = {t.name for t in listed if t.annotations and t.annotations.destructive_hint}
    assert destructive <= {"jira_delete_issue", "merge_merge_request", "delete_branch"}


def test_mcp_requires_token(tools: FakeTools) -> None:
    assert httpx.post(tools.mcp_url("jira"), json={}).status_code == 401


async def test_jira_mcp_read_and_write_tokens(tools: FakeTools) -> None:
    tools.reset()
    async with client(tools, "jira", "read") as c:
        found = await c.call_tool("jira_search", {"jql": "key = SECOPS-4791"})
        assert not found.is_error and found.structured_content["total"] == 1
        issue = await c.call_tool("jira_get_issue", {"issue_key": "SECOPS-4791"})
        assert json.loads(issue.content[0].text)["key"] == "SECOPS-4791"
        denied = await c.call_tool("jira_create_issue", {"project_key": "SECOPS", "summary": "x"})
        assert denied.is_error and "403" in denied.content[0].text
        invalid = await c.call_tool("jira_get_issue", {})
        assert invalid.is_error
    async with client(tools, "jira", "write") as c:
        created = await c.call_tool("jira_create_issue", {"project_key": "SECOPS", "summary": "Upgrade log4j"})
        assert not created.is_error and created.structured_content["key"].startswith("SECOPS-")
        comment = await c.call_tool("jira_add_comment", {"issue_key": "SECOPS-4791", "comment": "Same finding"})
        assert not comment.is_error
    writes = tools.writes()
    assert [w["type"] for w in writes] == ["jira.issues.create", "jira.comments.create"]
    assert all(w["token_kind"] == "write" for w in writes)
    assert any(d["what"] == "mcp jira_create_issue" for d in tools.state()["denied"])


async def test_gitlab_and_confluence_mcp(tools: FakeTools) -> None:
    tools.reset()
    async with client(tools, "gitlab", "read") as c:
        projects = await c.call_tool("list_projects", {"group": "infra"})
        assert projects.structured_content["total"] == 4
        pom = await c.call_tool("get_file_contents", {"project_id": "infra/payments-api", "file_path": "pom.xml"})
        assert "2.14.1" in pom.structured_content["content"]
        tree = await c.call_tool("get_file_contents", {"project_id": "101", "file_path": "src"})
        assert tree.structured_content["type"] == "tree"
        diffs = await c.call_tool("get_merge_request_diffs", {"project_id": "101", "merge_request_iid": 1})
        assert diffs.structured_content["diffs"][0]["new_path"] == "README.md"
        merge = await c.call_tool("merge_merge_request", {"project_id": "101", "merge_request_iid": 1})
        assert merge.is_error
    async with client(tools, "confluence", "read") as c:
        hits = await c.call_tool("confluence_search", {"query": "flow matrix"})
        assert hits.structured_content["results"][0]["id"] == "10002"
        page = await c.call_tool("confluence_get_page", {"page_id": "10001"})
        assert page.structured_content["title"] == "SSO portal architecture"
        create = await c.call_tool("confluence_create_page", {"space_key": "SEC", "title": "t", "content": "c"})
        assert create.is_error
    assert tools.writes() == []
