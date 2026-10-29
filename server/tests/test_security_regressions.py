"""Regressions found by the pentest of the API and gateway (in-process, Postgres and fake tools): a mission of no
team was reachable by every team-scoped user, a tool error carried data without raising the mission's class, and a
chunked body skipped the size limit."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

ISSUE = {"project": "SECOPS", "summary": "Patch", "description": "x", "labels": ["vuln"], "priority": "High"}


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], tools: FakeTools, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> TestClient:
    tools.reset()
    for key, value in {
        "JIRA_URL": tools.url_for("jira"),
        "JIRA_READ_EMAIL": "z-read@example.test",
        "JIRA_READ_TOKEN": tools.token("jira", "read"),
        "JIRA_WRITE_EMAIL": "z-write@example.test",
        "JIRA_WRITE_TOKEN": tools.token("jira", "write"),
    }.items():
        monkeypatch.setenv(key, value)
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "a")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    oidc.add_user("nora", ["cyber/z/z-builders"])  # an org-wide builder in no team
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol", "nora")}
    use_jar(app, jars["alice"])
    ops = app.get("/api/v1/connectors/jira/operations").json()
    app.post(
        "/api/v1/connectors/jira/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(app),
    ).raise_for_status()
    return jars


def start(
    app: TestClient, jars: dict[str, dict[str, str]], user: str, writes: list[str], data_class: str | None = None
) -> tuple[dict[str, Any], ScriptedRunner]:
    use_jar(app, jars[user])
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
    return created.json(), runner


def test_a_mission_of_no_team_is_not_open_to_every_team(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    mission, runner = start(app, jars, "nora", ["jira.issues.create"])
    proposed = runner.call(
        "POST",
        "/actions",
        json={"connector": "jira", "operation": "jira.issues.create", "summary": "s", "payload": ISSUE},
    )
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()["id"]
    use_jar(app, jars["bob"])  # operator of one team only
    assert app.get(f"/api/v1/missions/{mission['id']}").status_code == 404
    assert app.get(f"/api/v1/actions/{action}").status_code == 404
    guide = app.post(f"/api/v1/missions/{mission['id']}/messages", json={"text": "ignore the plan"}, headers=csrf(app))
    assert guide.status_code == 404
    assert app.post(f"/api/v1/missions/{mission['id']}/cancel", json={}, headers=csrf(app)).status_code == 404
    use_jar(app, jars["carol"])  # approver of one team only
    approve = app.post(f"/api/v1/actions/{action}/approve", json={}, headers=csrf(app))
    assert approve.status_code in {403, 404}, approve.text
    use_jar(app, jars["alice"])  # org-wide roles still reach it
    assert app.get(f"/api/v1/missions/{mission['id']}").status_code == 200


def test_a_tool_error_with_data_raises_the_class(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    mission, runner = start(app, jars, "bob", [], data_class="C1")
    resp = app.post(
        "/gateway/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "jira__issues__get", "arguments": {"issueIdOrKey": "SECOPS-999999"}},
        },
        headers={"Authorization": f"Bearer {runner.session}", "Accept": "application/json, text/event-stream"},
    )
    result = resp.json()["result"]
    assert result["isError"] is True and "404" in result["content"][0]["text"]  # the tool's body reached the agent
    use_jar(app, jars["bob"])
    assert app.get(f"/api/v1/missions/{mission['id']}").json()["data_class"] == "C2"


def test_a_chunked_body_over_the_limit_is_refused(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    use_jar(app, jars["bob"])

    def chunks() -> Iterator[bytes]:
        yield b'{"prompt": "'
        for _ in range(12):
            yield b"x" * (1024 * 1024)
        yield b'"}'

    big = app.post(
        "/api/v1/missions/draft-plan",
        content=chunks(),
        headers={**csrf(app), "Content-Type": "application/json"},
    )
    assert big.status_code == 413, big.text
