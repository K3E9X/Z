"""Slack through the workplace pack connector (Web API subset): reads with the read token, posts proposed, approved
and applied with the write token, Slack's HTTP 200 `ok: false` answers treated as errors (error_body), a chat
preview for the reviewer, and no duplicate post after a crash."""

from __future__ import annotations

import json
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

CHANNEL = "C0Z7SECOPS"


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], tools: FakeTools, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> TestClient:
    tools.reset()
    monkeypatch.setenv("SLACK_API_URL", tools.url_for("slack") + "/api")
    monkeypatch.setenv("SLACK_TOKEN_RO", tools.token("slack", "read"))
    monkeypatch.setenv("SLACK_TOKEN_RW", tools.token("slack", "write"))
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "a")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}
    use_jar(app, jars["alice"])
    ops = app.get("/api/v1/connectors/slack/operations").json()
    assert {o["id"]: o["class"] for o in ops} == {
        "slack.channels.list": "read",
        "slack.messages.list": "read",
        "slack.threads.get": "read",
        "slack.messages.post": "write",
        "slack.messages.delete": "destructive",
    }
    approved = app.post(
        "/api/v1/connectors/slack/approve-exposure",
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
        json={"title": "Follow up", "prompt": "Follow up", "plan": plan, "connectors": ["slack"]},
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


def test_reads_and_slack_errors(app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools) -> None:
    runner = start(app, jars, [])
    history = call(app, runner, "slack__messages__list", {"channel": CHANNEL})
    assert history["isError"] is False
    assert "patch window moved" in history["content"][0]["text"].lower()
    missing = call(app, runner, "slack__messages__list", {"channel": "C0NOPE0000"})
    assert missing["isError"] is True and "channel_not_found" in missing["content"][0]["text"]
    write = call(app, runner, "slack__messages__post", {"body": {"channel": CHANNEL, "text": "hi"}})
    assert write["isError"] is True and tools.writes("slack.messages.post") == []


def test_a_bad_token_fails_the_connection_test(
    app: TestClient, jars: dict[str, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    use_jar(app, jars["alice"])
    assert app.post("/api/v1/connectors/slack/test", json={}, headers=csrf(app)).json()["ok"] is True
    monkeypatch.setenv("SLACK_TOKEN_RO", "not-a-slack-token")
    app.app.state.z.gateway.resolver.forget()  # type: ignore[attr-defined]  # as after a rotation
    tested = app.post("/api/v1/connectors/slack/test", json={}, headers=csrf(app)).json()
    assert tested["ok"] is False and "invalid_auth" in tested["detail"]  # HTTP 200, ok: false


def propose(runner: ScriptedRunner, body: dict[str, Any]) -> dict[str, Any]:
    proposed = runner.call(
        "POST",
        "/actions",
        json={"connector": "slack", "operation": "slack.messages.post", "summary": "Reply", "payload": {"body": body}},
    )
    assert proposed.status_code == 201, proposed.text
    return dict(proposed.json())


def approve(app: TestClient, jars: dict[str, dict[str, str]], action_id: str) -> None:
    use_jar(app, jars["carol"])
    approved = app.post(f"/api/v1/actions/{action_id}/approve", json={}, headers=csrf(app))
    assert approved.status_code == 200, approved.text


def test_a_reply_is_proposed_previewed_applied_once(
    app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools
) -> None:
    runner = start(app, jars, ["slack.messages.post"])
    history = call(app, runner, "slack__messages__list", {"channel": CHANNEL})["content"][0]["text"]
    thread_ts = next(m["ts"] for m in json.loads(history)["messages"] if m.get("reply_count"))  # the seeded thread
    action = propose(runner, {"channel": CHANNEL, "thread_ts": thread_ts, "text": "Done: edge-01 patched"})
    assert action["preview"] == {
        "kind": "chat_message",
        "platform": "slack",
        "target": f"{CHANNEL} (reply to {thread_ts})",
        "text": "Done: edge-01 patched",
    }
    sneaky = runner.call(
        "POST",
        "/actions",
        json={
            "connector": "slack",
            "operation": "slack.messages.post",
            "summary": "Reply",
            "payload": {"body": {"channel": CHANNEL, "text": "x", "metadata": {"event_type": "z_action"}}},
        },
    )
    assert sneaky.status_code == 422  # the idempotency marker is Z's, never the agent's
    approve(app, jars, action["id"])
    assert executor_tick(app)["applied"] == [action["id"]]
    [write] = tools.writes("slack.messages.post")
    assert write["token_kind"] == "write" and write["data"]["thread_ts"] == thread_ts
    assert write["data"]["metadata"] == {"event_type": "z_action", "event_payload": {"action": action["id"]}}
    applied = app.get(f"/api/v1/actions/{action['id']}").json()
    assert applied["status"] == "applied" and applied["result"]["thread_ts"] == thread_ts

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
    assert len(tools.writes("slack.messages.post")) == 1
    recovered = app.get(f"/api/v1/actions/{action['id']}").json()
    assert recovered["status"] == "applied" and recovered["result"]["recovered"] is True


def test_a_post_slack_refuses_fails_instead_of_counting_as_applied(
    app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools
) -> None:
    runner = start(app, jars, ["slack.messages.post"])
    action = propose(runner, {"channel": "C0GONE0000", "text": "Status update"})
    approve(app, jars, action["id"])
    assert executor_tick(app)["failed"] == [action["id"]]
    failed = app.get(f"/api/v1/actions/{action['id']}").json()
    assert failed["status"] == "failed" and "channel_not_found" in failed["result"]["error"]
    assert tools.writes("slack.messages.post") == []
