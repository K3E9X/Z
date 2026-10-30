"""`/mcp`: Z as an MCP server (docs/03 §5) — bearer tokens only, each tool is the REST operation with its checks."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.server import csrf, seed_packs, sign_in_as

ACCEPT = "application/json, text/event-stream"


@pytest.fixture
def app(make_client: Callable[..., TestClient], tmp_path: Any) -> TestClient:
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "artifacts")
    seed_packs(client)
    return client


def _token(app: TestClient, oidc: FakeGitLabOIDC, user: str, scopes: list[str]) -> str:
    sign_in_as(app, oidc, user)
    created = app.post(
        "/api/v1/me/tokens", json={"name": "mcp", "scopes": scopes, "expires_in_days": 7}, headers=csrf(app)
    )
    assert created.status_code == 201, created.text
    app.cookies.clear()
    return str(created.json()["token"])


def rpc(app: TestClient, token: str | None, method: str, params: dict[str, Any] | None = None) -> Any:
    headers = {"Accept": ACCEPT, "User-Agent": "claude-code/9"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = app.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def tool(app: TestClient, token: str, name: str, args: dict[str, Any]) -> tuple[bool, str]:
    result = rpc(app, token, "tools/call", {"name": name, "arguments": args})
    return bool(result.get("isError")), str(result["content"][0]["text"])


def test_tools_are_listed_with_honest_annotations(app: TestClient, oidc: FakeGitLabOIDC) -> None:
    token = _token(app, oidc, "bob", ["missions:read"])
    tools = {t["name"]: t for t in rpc(app, token, "tools/list")["tools"]}
    assert set(tools) == {"start_mission", "get_mission", "list_playbooks", "list_review"}
    assert tools["start_mission"]["annotations"]["readOnlyHint"] is False
    assert all(tools[n]["annotations"]["readOnlyHint"] for n in ("get_mission", "list_playbooks", "list_review"))


def test_a_token_is_required_and_cookies_are_not_enough(app: TestClient, oidc: FakeGitLabOIDC) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    assert app.post("/mcp", json=body, headers={"Accept": ACCEPT}).status_code == 401
    sign_in_as(app, oidc, "bob")  # a signed-in browser session
    assert app.post("/mcp", json=body, headers={"Accept": ACCEPT}).status_code == 401
    app.cookies.clear()
    bad = app.post("/mcp", json=body, headers={"Accept": ACCEPT, "Authorization": "Bearer zt_nope"})
    assert bad.status_code == 401 and bad.headers["www-authenticate"].startswith("Bearer")


def test_start_and_follow_a_mission_through_the_api_checks(app: TestClient, oidc: FakeGitLabOIDC) -> None:
    token = _token(app, oidc, "bob", ["missions:start", "missions:read", "playbooks:read"])
    failed, text = tool(app, token, "list_playbooks", {"q": "summarize"})
    assert not failed and any(p["name"] == "summarize-file" for p in json.loads(text)["items"])
    failed, text = tool(
        app, token, "start_mission", {"playbook": "summarize-file", "inputs": {"file": "a.md"}, "data_class": "C1"}
    )
    assert not failed, text
    mission = json.loads(text)
    assert mission["url"] == f"https://testserver/missions/{mission['id']}"
    failed, text = tool(app, token, "get_mission", {"id": mission["id"]})
    assert not failed and json.loads(text)["id"] == mission["id"] and "job_list" in json.loads(text)
    failed, text = tool(app, token, "list_review", {"mission": mission["id"]})
    assert not failed and "items" in json.loads(text)
    sign_in_as(app, oidc, "alice")
    created = app.get("/api/v1/audit", params={"action": "mission.created"}).json()["items"]
    entry = next(e for e in created if e["target_id"] == mission["id"])
    assert entry["actor"] == "bob" and entry["user_agent"].startswith("z-mcp (claude-code/9")


def test_token_scopes_and_inputs_still_limit_what_a_client_can_do(app: TestClient, oidc: FakeGitLabOIDC) -> None:
    token = _token(app, oidc, "bob", ["missions:read"])
    failed, text = tool(app, token, "start_mission", {"prompt": "close every ticket"})
    assert failed and text.startswith("Z refused (403)")
    failed, text = tool(app, token, "get_mission", {"id": "../admin/users"})
    assert failed and "is an id" in text
    failed, text = tool(app, token, "get_mission", {"id": "mis_missing"})
    assert failed and text.startswith("Z refused (404)")
    failed, text = tool(app, token, "start_mission", {"prompt": "x", "start": False, "autonomy": "auto"})
    assert failed and "unknown arguments" in text
