"""Mattermost through the workplace pack connector (API v4 subset): reads with the read token, posts proposed,
approved and applied with the write token, a chat preview for the reviewer, and no duplicate post after a crash."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

CHANNEL, ROOT = "c" * 26, "p" * 26


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], tools: FakeTools, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> TestClient:
    tools.reset()
    monkeypatch.setenv("MATTERMOST_URL", tools.url_for("mattermost"))
    monkeypatch.setenv("MATTERMOST_TOKEN_RO", tools.token("mattermost", "read"))
    monkeypatch.setenv("MATTERMOST_TOKEN_RW", tools.token("mattermost", "write"))
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "a")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}
    use_jar(app, jars["alice"])
    ops = app.get("/api/v1/connectors/mattermost/operations").json()
    assert {o["id"]: o["class"] for o in ops if o["id"].startswith("mattermost.posts.")} == {
        "mattermost.posts.list": "read",
        "mattermost.posts.get": "read",
        "mattermost.posts.create": "write",
        "mattermost.posts.delete": "destructive",
    }
    approved = app.post(
        "/api/v1/connectors/mattermost/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(app),
    )
    assert approved.status_code == 200, approved.text
    return jars


def start(app: TestClient, jars: dict[str, dict[str, str]], writes: list[str]) -> ScriptedRunner:
    use_jar(app, jars["bob"])
    plan = {"steps": [{"id": "s", "agent": "orchestrator", "prompt": "Follow up", "writes": writes}], "writes": writes}
    created = app.post(
        "/api/v1/missions",
        json={"title": "Follow up", "prompt": "Follow up", "plan": plan, "connectors": ["mattermost"]},
        headers=csrf(app),
    )
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    return runner


def call(app: TestClient, runner: ScriptedRunner, name: str, args: dict[str, Any]) -> dict[str, Any]:
    resp = app.post(
        "/gateway/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
        headers={"Authorization": f"Bearer {runner.session}", "Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["result"])


def executor_tick(app: TestClient) -> dict[str, Any]:
    from z_server.gateway import executor

    state = app.app.state.z  # type: ignore[attr-defined]
    return app.portal.call(executor.tick, state, state.gateway)  # type: ignore[union-attr,no-any-return]


def test_reads_use_the_read_token(app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools) -> None:
    runner = start(app, jars, [])
    team = call(app, runner, "mattermost__teams__get_by_name", {"team_name": "secops"})
    assert team["isError"] is False and "t" * 26 in team["content"][0]["text"]
    thread = call(app, runner, "mattermost__threads__get", {"post_id": ROOT})
    text = thread["content"][0]["text"]
    assert thread["isError"] is False and "Patch window" in text and "ack, netops" in text
    write = call(app, runner, "mattermost__posts__create", {"body": {"channel_id": CHANNEL, "message": "hi"}})
    assert write["isError"] is True  # writes are proposals, never tool calls
    assert tools.writes("mattermost.posts.create") == []


def test_a_reply_is_proposed_previewed_applied_once(
    app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools
) -> None:
    runner = start(app, jars, ["mattermost.posts.create"])
    payload = {"body": {"channel_id": CHANNEL, "root_id": ROOT, "message": "Done: edge-01 patched"}}
    proposed = runner.call(
        "POST",
        "/actions",
        json={
            "connector": "mattermost",
            "operation": "mattermost.posts.create",
            "summary": "Reply",
            "payload": payload,
        },
    )
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    assert action["preview"] == {
        "kind": "chat_message",
        "platform": "mattermost",
        "target": f"{CHANNEL} (reply to {ROOT})",
        "text": "Done: edge-01 patched",
    }
    sneaky = runner.call(
        "POST",
        "/actions",
        json={
            "connector": "mattermost",
            "operation": "mattermost.posts.create",
            "summary": "Reply",
            "payload": {"body": {**payload["body"], "props": {"z_action": "act_other"}}},
        },
    )
    assert sneaky.status_code == 422  # the agent cannot set props (the idempotency marker is Z's)
    use_jar(app, jars["carol"])
    approved = app.post(f"/api/v1/actions/{action['id']}/approve", json={}, headers=csrf(app))
    assert approved.status_code == 200, approved.text
    assert executor_tick(app)["applied"] == [action["id"]]
    [write] = tools.writes("mattermost.posts.create")
    assert write["token_kind"] == "write" and write["data"]["root_id"] == ROOT
    assert write["data"]["props"] == {"z_action": action["id"]}
    applied = app.get(f"/api/v1/actions/{action['id']}").json()
    assert applied["status"] == "applied" and applied["result"]["root_id"] == ROOT

    # crash between the post and the bookkeeping: recovery finds the post by its marker, no second post
    state = app.app.state.z  # type: ignore[attr-defined]

    async def crash() -> None:
        from z_server.db.models import ProposedAction

        async with state.sessions() as db, db.begin():
            await db.execute(
                update(ProposedAction)
                .where(ProposedAction.id == action["id"])
                .values(status="applying", updated_at=ProposedAction.updated_at - timedelta(hours=1))
            )

    app.portal.call(crash)  # type: ignore[union-attr]
    assert executor_tick(app)["recovered"] == [action["id"]]
    assert len(tools.writes("mattermost.posts.create")) == 1
    recovered = app.get(f"/api/v1/actions/{action['id']}").json()
    assert recovered["status"] == "applied" and recovered["result"]["recovered"] is True
