"""Tool gateway and action executor against the fake tools (real HTTP, Jira Cloud REST v3 surface)."""

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

ISSUE = {
    "project": "SECOPS",
    "summary": "Patch openssl on edge-01",
    "description": "**Critical** CVE.\n\n- edge-01\n- edge-02",
    "labels": ["vuln"],
    "priority": "High",
}


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def env(tools: FakeTools, monkeypatch: pytest.MonkeyPatch) -> FakeTools:
    tools.reset()
    for k, v in {
        "JIRA_URL": tools.url_for("jira"),
        "JIRA_READ_EMAIL": "z-read@example.test",
        "JIRA_READ_TOKEN": tools.token("jira", "read"),
        "JIRA_WRITE_EMAIL": "z-write@example.test",
        "JIRA_WRITE_TOKEN": tools.token("jira", "write"),
    }.items():
        monkeypatch.setenv(k, v)
    return tools


@pytest.fixture
def keys() -> RecordingKeyAdmin:
    return RecordingKeyAdmin()


@pytest.fixture
def app(make_client: Callable[..., TestClient], keys: RecordingKeyAdmin, tmp_path: Any, env: FakeTools) -> TestClient:
    client = make_client(llm_keys=keys, artifacts_dir=tmp_path / "artifacts")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}
    use_jar(app, jars["alice"])
    ops = app.get("/api/v1/connectors/jira/operations").json()
    resp = app.post(
        "/api/v1/connectors/jira/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(app),
    )
    assert resp.status_code == 200, resp.text
    return jars


def run(app: TestClient, fn: Callable[[Any], Any]) -> Any:
    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> Any:
        async with state.sessions() as db, db.begin():
            return await fn(db)

    return app.portal.call(go)  # type: ignore[union-attr]


def mcp(app: TestClient, token: str, method: str, params: dict[str, Any] | None = None) -> Any:
    resp = app.post(
        "/gateway/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def start(
    app: TestClient, jars: dict[str, dict[str, str]], writes: list[str], data_class: str | None = None
) -> tuple[str, ScriptedRunner]:
    use_jar(app, jars["bob"])
    plan = {"steps": [{"id": "act", "agent": "ticket-manager", "prompt": "Triage", "writes": writes}], "writes": writes}
    body: dict[str, Any] = {"title": "Triage", "prompt": "Triage", "plan": plan, "connectors": ["jira"]}
    if data_class:
        body["data_class"] = data_class
    created = app.post("/api/v1/missions", json=body, headers=csrf(app))
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    return str(created.json()["id"]), runner


def call(app: TestClient, runner: ScriptedRunner, name: str, args: dict[str, Any]) -> dict[str, Any]:
    out = mcp(app, runner.session or "", "tools/call", {"name": name, "arguments": args})
    assert "result" in out, out
    return dict(out["result"])


# ---------------------------------------------------------------- reads


def test_tools_list_is_the_job_allowlist(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    _, runner = start(app, jars, [])
    listed = {t["name"] for t in mcp(app, runner.session or "", "tools/list")["result"]["tools"]}
    assert listed == {
        "jira__issues__search",
        "jira__issues__get",
    }  # ticket-manager's gateway patterns, exposed reads only
    assert {t["name"] for t in runner.spec["tools"]["gateway"]} == listed
    assert app.post("/gateway/mcp", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_search_uses_the_read_account_and_flattens_adf(
    app: TestClient, jars: dict[str, dict[str, str]], env: FakeTools
) -> None:
    _, runner = start(app, jars, [])
    result = call(
        app,
        runner,
        "jira__issues__search",
        {"jql": "project = SECOPS", "maxResults": 2, "fields": "summary,description,status"},
    )
    assert result["isError"] is False and result["_meta"]["io.z/judge"] in {"clean", "flagged"}
    text = result["content"][0]["text"]
    assert '"issues"' in text and '"type":"doc"' not in text and "avatarUrls" not in text
    assert env.state()["denied"] == []
    use_jar(app, jars["alice"])
    page = app.get("/api/v1/audit", params={"action": "gateway.call", "limit": 50})
    assert page.status_code == 200, page.text
    entries = page.json()["items"]
    assert (
        entries and entries[0]["runs_as"] == "z-read@example.test" and entries[0]["target_id"] == "jira.issues.search"
    )


def test_not_allowed_and_write_calls_are_refused(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    mission_id, runner = start(app, jars, ["jira.issues.create"])
    refused = call(app, runner, "jira__issues__create", {"project": "SECOPS", "summary": "x", "description": "y"})
    assert refused["isError"] is True and "allowlist" in refused["content"][0]["text"]
    bad = call(app, runner, "jira__issues__search", {"maxResults": 5})
    assert bad["isError"] is True and "Invalid arguments" in bad["content"][0]["text"]
    use_jar(app, jars["bob"])
    denied = [e for e in app.get(f"/api/v1/missions/{mission_id}/replay").json()["events"] if e["type"] == "denied"]
    assert denied and denied[0]["payload"]["rule"] == "not_in_allowlist"


def test_injected_ticket_is_withheld_and_pauses_the_job(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    mission_id, runner = start(app, jars, [])
    result = call(app, runner, "jira__issues__get", {"issueIdOrKey": "SECOPS-4795"})
    assert result["isError"] is True and result["_meta"]["io.z/judge"] == "blocked"
    assert (
        "withheld" in result["content"][0]["text"] and "ignore all previous" not in result["content"][0]["text"].lower()
    )
    control = runner.control()
    assert control["state"] == "pause"
    use_jar(app, jars["bob"])
    job = app.get(f"/api/v1/missions/{mission_id}/jobs").json()["items"][0]
    assert job["status"] == "needs_attention" and job["needs_attention_reason"] == "judge_blocked"

    # a person sees it: a decision in Review and a judge.blocked event in History
    review = app.get("/api/v1/review", params={"mission": mission_id}).json()
    [item] = [i for i in review["items"] if i["kind"] == "question"]
    assert review["counts"]["questions"] == 1 and "withheld a result of jira.issues.get" in item["question"]["text"]
    assert item["question"]["options"] == ["Continue without it", "Stop the mission"]
    use_jar(app, jars["alice"])
    audit = app.get("/api/v1/audit", params={"action": "judge.blocked"}).json()["items"]
    assert audit and audit[0]["target_id"] == job["id"]
    # releasing it lets the agent go on without the withheld content
    use_jar(app, jars["bob"])
    answered = app.post(
        f"/api/v1/questions/{item['question']['id']}/answer", json={"answer": "Continue without it"}, headers=csrf(app)
    )
    assert answered.status_code == 200, answered.text
    assert runner.control()["state"] == "run"
    assert app.get(f"/api/v1/missions/{mission_id}/jobs").json()["items"][0]["status"] == "running"


def test_stopping_after_a_judge_block_cancels_the_mission(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    mission_id, runner = start(app, jars, [])
    call(app, runner, "jira__issues__get", {"issueIdOrKey": "SECOPS-4795"})
    use_jar(app, jars["bob"])
    [item] = [
        i for i in app.get("/api/v1/review", params={"mission": mission_id}).json()["items"] if i["kind"] == "question"
    ]
    stop = app.post(
        f"/api/v1/questions/{item['question']['id']}/answer", json={"answer": "Stop the mission"}, headers=csrf(app)
    )
    assert stop.status_code == 200, stop.text
    assert app.get(f"/api/v1/missions/{mission_id}").json()["status"] == "cancelled"


def test_c2_result_raises_a_c1_mission_before_returning(
    app: TestClient, jars: dict[str, dict[str, str]], keys: RecordingKeyAdmin
) -> None:
    mission_id, runner = start(app, jars, [], data_class="C1")
    call(app, runner, "jira__issues__search", {"jql": "project = SECOPS", "maxResults": 1})
    use_jar(app, jars["bob"])
    assert app.get(f"/api/v1/missions/{mission_id}").json()["data_class"] == "C2"
    assert keys.calls[-1][0] == "set_models"


def test_rate_limit_per_mission(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    from z_server.db.models import Connector

    async def tighten(db: Any) -> None:
        row = (
            await db.execute(__import__("sqlalchemy").select(Connector).where(Connector.name == "jira"))
        ).scalar_one()
        row.manifest = {**row.manifest, "rate_limit": {"per_mission_per_minute": 2}}

    run(app, tighten)
    _, runner = start(app, jars, [])
    args = {"jql": "project = SECOPS", "maxResults": 1}
    assert call(app, runner, "jira__issues__search", args)["isError"] is False
    assert call(app, runner, "jira__issues__search", args)["isError"] is False
    limited = call(app, runner, "jira__issues__search", args)
    assert limited["isError"] is True and "Rate limited" in limited["content"][0]["text"]


def test_large_results_become_artifacts(
    app: TestClient, jars: dict[str, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from z_server.gateway import core

    monkeypatch.setattr(core, "MAX_RESULT_CHARS", 300)
    _, runner = start(app, jars, [])
    result = call(app, runner, "jira__issues__search", {"jql": "project = SECOPS", "maxResults": 10})
    assert result["_meta"]["io.z/truncated"] is True and result["_meta"]["io.z/artifact"].startswith("art_")
    assert "truncated" in result["content"][0]["text"]


# ---------------------------------------------------------------- executor


def executor_tick(app: TestClient) -> dict[str, list[str]]:
    from z_server.gateway import executor

    state = app.app.state.z  # type: ignore[attr-defined]
    return app.portal.call(executor.tick, state, state.gateway)  # type: ignore[union-attr,no-any-return]


def propose_and_approve(
    app: TestClient, jars: dict[str, dict[str, str]], runner: ScriptedRunner, operation: str, payload: dict[str, Any]
) -> str:
    proposed = runner.call(
        "POST", "/actions", json={"connector": "jira", "operation": operation, "summary": "do it", "payload": payload}
    )
    assert proposed.status_code == 201, proposed.text
    action_id = str(proposed.json()["id"])
    use_jar(app, jars["carol"])
    approved = app.post(f"/api/v1/actions/{action_id}/approve", json={}, headers=csrf(app))
    assert approved.status_code == 200 and approved.json()["status"] == "approved", approved.text
    return action_id


def test_executor_creates_the_issue_with_the_write_account(
    app: TestClient, jars: dict[str, dict[str, str]], env: FakeTools
) -> None:
    _, runner = start(app, jars, ["jira.issues.create", "jira.comments.create"])
    action_id = propose_and_approve(app, jars, runner, "jira.issues.create", ISSUE)
    report = executor_tick(app)
    assert report["applied"] == [action_id], report
    action = app.get(f"/api/v1/actions/{action_id}").json()
    assert (
        action["status"] == "applied"
        and action["result"]["key"].startswith("SECOPS-")
        and action["result"]["url"].endswith(action["result"]["key"])
    )
    [write] = env.writes("jira.issues.create")
    assert write["token_kind"] == "write"
    fields = write["data"]["fields"]
    assert f"z-{action_id.lower()}" in fields["labels"] and "vuln" in fields["labels"]
    assert fields["priority"] == {"name": "High"}

    # crash between the write and the bookkeeping: recovery finds the issue by label, no duplicate
    async def crash(db: Any) -> None:
        from z_server.db.models import ProposedAction

        await db.execute(
            update(ProposedAction)
            .where(ProposedAction.id == action_id)
            .values(status="applying", updated_at=ProposedAction.updated_at - timedelta(hours=1))
        )

    run(app, crash)
    assert executor_tick(app)["recovered"] == [action_id]
    assert len(env.writes("jira.issues.create")) == 1
    recovered = app.get(f"/api/v1/actions/{action_id}").json()
    assert recovered["status"] == "applied" and recovered["result"]["recovered"] is True

    comment_id = propose_and_approve(
        app, jars, runner, "jira.comments.create", {"issue": action["result"]["key"], "body": "Fixed in **2.4**"}
    )
    assert executor_tick(app)["applied"] == [comment_id]
    [comment] = env.writes("jira.comments.create")
    assert comment["data"]["body"] == "Fixed in 2.4"  # ADF sent, the fake stores its text


def test_executor_refuses_when_preconditions_changed(
    app: TestClient, jars: dict[str, dict[str, str]], env: FakeTools
) -> None:
    _, runner = start(app, jars, ["jira.issues.create"])
    action_id = propose_and_approve(app, jars, runner, "jira.issues.create", ISSUE)
    use_jar(app, jars["alice"])
    hidden = app.patch("/api/v1/operations/jira.issues.create", json={"exposed": False}, headers=csrf(app))
    assert hidden.status_code == 200, hidden.text
    assert executor_tick(app)["failed"] == [action_id]
    action = app.get(f"/api/v1/actions/{action_id}").json()
    assert action["status"] == "failed" and "no longer exposed" in action["result"]["error"]
    assert env.writes("jira.issues.create") == []


def test_tool_errors_fail_the_action_with_the_reason(
    app: TestClient, jars: dict[str, dict[str, str]], env: FakeTools
) -> None:
    _, runner = start(app, jars, ["jira.issues.create"])
    action_id = propose_and_approve(app, jars, runner, "jira.issues.create", {**ISSUE, "project": "NOPE"})
    assert executor_tick(app)["failed"] == [action_id]
    action = app.get(f"/api/v1/actions/{action_id}").json()
    assert "create issue: HTTP 400" in action["result"]["error"] and "project" in action["result"]["error"]
