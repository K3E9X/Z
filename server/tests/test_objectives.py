"""Objectives (docs/04 §3): measuring, status, suggestions in Review, read-only auto-start within budget,
assignments, the scheduler tick and the agent's report_objective."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

READ_ONLY_PLAYBOOK = {
    "name": "findings-digest",
    "version": "1.0.0",
    "description": "Summarise untriaged findings.",
    "team": ["vuln-analyst"],
    "steps": [{"id": "digest", "agent": "vuln-analyst", "prompt": "Summarise the untriaged findings."}],
    "budget": {"max_tokens": 1000},
}


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as t:
        yield t


@pytest.fixture
def app(make_client: Callable[..., TestClient], tools: FakeTools, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    tools.reset()
    for k, v in {
        "JIRA_URL": tools.url_for("jira"),
        "JIRA_READ_EMAIL": "r@example.test",
        "JIRA_READ_TOKEN": tools.token("jira", "read"),
        "JIRA_WRITE_EMAIL": "w@example.test",
        "JIRA_WRITE_TOKEN": tools.token("jira", "write"),
    }.items():
        monkeypatch.setenv(k, v)
    client = make_client(llm_keys=RecordingKeyAdmin())
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "erin")}
    use_jar(app, jars["alice"])
    ops = app.get("/api/v1/connectors/jira/operations").json()
    app.post(
        "/api/v1/connectors/jira/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(app),
    ).raise_for_status()
    use_jar(app, jars["erin"])  # builder: edits agents and objectives
    return jars


def run_db(app: TestClient, fn: Callable[[Any], Any]) -> Any:
    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> Any:
        async with state.sessions() as db, db.begin():
            return await fn(db)

    return app.portal.call(go)  # type: ignore[union-attr]


def old_tasks(app: TestClient, n: int, hours: int = 72) -> None:
    from z_server.db.models import Task
    from z_server.ids import new_id

    async def add(db: Any) -> None:
        for i in range(n):
            db.add(
                Task(
                    id=new_id("tsk"),
                    source="webhook",
                    external_ref=f"f-{i}",
                    title=f"Finding {i}",
                    status="incoming",
                    created_at=datetime.now(UTC) - timedelta(hours=hours),
                )
            )

    run_db(app, add)


def objective(app: TestClient, **extra: Any) -> dict[str, Any]:
    body = {
        "title": "Findings triaged within 48 hours",
        "metric": {
            "source": "z",
            "query": "untriaged_findings",
            "params": {"older_than_hours": 48},
            "target": 0,
            "direction": "down",
        },
        "cadence": "0 */4 * * *",
        **extra,
    }
    resp = app.post("/api/v1/agents/vuln-analyst/objectives", json=body, headers=csrf(app))
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def test_create_validate_pause_and_resume(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    obj = objective(app, allowed_playbooks=["weekly-vuln-triage"])
    assert obj["agent"] == "vuln-analyst" and obj["status"] == "on_track" and obj["owner_user"] == "erin"
    bad = app.post(
        "/api/v1/agents/vuln-analyst/objectives",
        json={**{k: obj[k] for k in ("title", "metric")}, "cadence": "every tuesday"},
        headers=csrf(app),
    )
    assert bad.status_code == 422 and "cadence" in bad.text
    bad = app.post(
        "/api/v1/agents/vuln-analyst/objectives",
        json={**{k: obj[k] for k in ("title", "metric", "cadence")}, "allowed_playbooks": ["nope"]},
        headers=csrf(app),
    )
    assert bad.status_code == 404
    paused = app.patch(f"/api/v1/objectives/{obj['id']}", json={"paused": True}, headers=csrf(app)).json()
    assert paused["status"] == "paused"
    assert app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app)).status_code == 409
    resumed = app.patch(
        f"/api/v1/objectives/{obj['id']}", json={"paused": False, "title": "Within 2 days"}, headers=csrf(app)
    )
    assert resumed.json()["status"] == "on_track" and resumed.json()["title"] == "Within 2 days"
    assert [o["id"] for o in app.get("/api/v1/agents/vuln-analyst/objectives").json()] == [obj["id"]]
    use_jar(app, jars["bob"])  # an operator cannot edit objectives
    assert app.patch(f"/api/v1/objectives/{obj['id']}", json={"paused": True}, headers=csrf(app)).status_code == 403


def test_gap_suggests_missions_in_review(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    obj = objective(app, allowed_playbooks=["weekly-vuln-triage"], scope=[{"kind": "product", "ref": "payments"}])
    resp = app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app))
    assert resp.status_code == 200 and resp.json()["status"] == "on_track" and resp.json()["suggestions"] == []
    old_tasks(app, 3)
    check = app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app)).json()
    assert check["value"] == 3 and check["status"] == "off_track" and len(check["suggestions"]) == 1
    assert "untriaged_findings is 3" in check["explanation"]
    again = app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app)).json()
    assert again["suggestions"] == check["suggestions"]  # the open suggestion is refreshed, not duplicated
    history = app.get(f"/api/v1/objectives/{obj['id']}/history").json()
    assert [h["value"] for h in history] == [3, 3, 0]
    assert app.get("/api/v1/agents/vuln-analyst/objectives").json()[0]["trend"] == [0, 3, 3]
    use_jar(app, jars["bob"])
    review = app.get("/api/v1/review", params={"kind": "suggestions"}).json()
    [item] = [i for i in review["items"] if i["kind"] == "suggestion"]
    suggestion = item["suggestion"]
    assert suggestion["playbook"] == "weekly-vuln-triage" and suggestion["has_writes"] is True
    assert suggestion["inputs"] == {"product": "payments"}
    accepted = app.post(f"/api/v1/suggestions/{suggestion['id']}/accept", json={}, headers=csrf(app))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["playbook"] == "weekly-vuln-triage"
    assert app.post(f"/api/v1/suggestions/{suggestion['id']}/dismiss", json={}, headers=csrf(app)).status_code == 409


def test_playbooks_missing_inputs_fall_back_to_a_free_form_suggestion(
    app: TestClient, jars: dict[str, dict[str, str]]
) -> None:
    obj = objective(app, allowed_playbooks=["weekly-vuln-triage"])
    old_tasks(app, 1)
    check = app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app)).json()
    assert "weekly-vuln-triage needs" in check["explanation"] and len(check["suggestions"]) == 1
    use_jar(app, jars["bob"])
    [item] = [i for i in app.get("/api/v1/review").json()["items"] if i["kind"] == "suggestion"]
    assert item["suggestion"].get("playbook") is None
    assert (
        app.post(f"/api/v1/suggestions/{item['suggestion']['id']}/accept", json={}, headers=csrf(app)).status_code
        == 200
    )


def test_free_form_suggestion_accept_and_dismiss(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    obj = objective(app)
    old_tasks(app, 1)
    [sid] = app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app)).json()["suggestions"]
    use_jar(app, jars["bob"])
    mission = app.post(f"/api/v1/suggestions/{sid}/accept", json={}, headers=csrf(app))
    assert mission.status_code == 200, mission.text
    assert mission.json()["plan"]["steps"][0]["agent"] == "vuln-analyst"
    use_jar(app, jars["erin"])
    old_tasks(app, 1)
    [sid2] = app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app)).json()["suggestions"]
    assert sid2 != sid
    use_jar(app, jars["bob"])
    assert (
        app.post(f"/api/v1/suggestions/{sid2}/dismiss", json={"reason": "known"}, headers=csrf(app)).status_code == 204
    )
    assert app.post(f"/api/v1/suggestions/{sid2}/accept", json={}, headers=csrf(app)).status_code == 409


def test_read_only_auto_start_respects_the_budget(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    use_jar(app, jars["alice"])
    assert app.post("/api/v1/playbooks", json=READ_ONLY_PLAYBOOK, headers=csrf(app)).status_code == 201
    obj = objective(
        app,
        allowed_playbooks=["findings-digest"],
        mode="auto_start_read_only",
        budget_per_period={"tokens": 1500, "period": "day"},
    )
    old_tasks(app, 2)
    first = app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app)).json()
    assert len(first["started_missions"]) == 1, first
    mission = app.get(f"/api/v1/missions/{first['started_missions'][0]}").json()
    assert mission["playbook"] == "findings-digest" and mission["status"] in {"running", "queued"}
    second = app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app)).json()
    assert second["started_missions"] == [] and "objective budget" in second["explanation"]


def test_stale_tickets_over_assigned_jira_projects(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    obj = objective(
        app,
        metric={"source": "z", "query": "stale_tickets", "params": {"days": 3}, "target": 0, "direction": "down"},
    )
    resp = app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app))
    assert resp.status_code == 503 and "no Jira project" in resp.json()["detail"]
    made = app.post(
        "/api/v1/agents/vuln-analyst/assignments",
        json={"object_kind": "jira_project", "object_ref": "SECOPS", "role": "owner"},
        headers=csrf(app),
    )
    assert made.status_code == 201 and made.json()["agent"] == "vuln-analyst"
    dup = app.post(
        "/api/v1/agents/vuln-analyst/assignments",
        json={"object_kind": "jira_project", "object_ref": "SECOPS"},
        headers=csrf(app),
    )
    assert dup.status_code == 409
    assert [a["object_ref"] for a in app.get("/api/v1/agents/vuln-analyst/assignments").json()] == ["SECOPS"]
    check = app.post(f"/api/v1/objectives/{obj['id']}/evaluate", json={}, headers=csrf(app)).json()
    assert check["value"] >= 1 and check["status"] == "off_track", check


def test_connector_metric_and_tick(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    from z_server import objectives

    obj = objective(
        app,
        metric={
            "source": "connector",
            "operation": "jira.issues.search",
            "params": {"jql": 'project = "SECOPS" AND statusCategory != Done', "maxResults": 50},
            "path": "issues",
            "target": 100,
            "direction": "down",
        },
    )
    state = app.app.state.z  # type: ignore[attr-defined]
    checked = app.portal.call(objectives.tick, state, datetime.now(UTC) + timedelta(minutes=1))  # type: ignore[union-attr]
    assert checked == [obj["id"]]
    [check] = app.get(f"/api/v1/objectives/{obj['id']}/history").json()
    assert check["status"] == "on_track" and 1 <= check["value"] < 100
    assert app.portal.call(objectives.tick, state, datetime.now(UTC) + timedelta(minutes=2)) == []  # type: ignore[union-attr]


def test_agent_reports_its_objective(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    obj = objective(app, allowed_playbooks=["weekly-vuln-triage"])
    use_jar(app, jars["bob"])
    created = app.post(
        "/api/v1/missions", json={"prompt": "Check the objective", "agents": ["vuln-analyst"]}, headers=csrf(app)
    )
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    resp = runner.call(
        "POST",
        "/objective-report",
        json={
            "objective_id": obj["id"],
            "value": 4,
            "explanation": "Four findings wait for triage.",
            "suggestions": [{"title": "Triage now", "playbook": "weekly-vuln-triage", "inputs": {}}],
        },
    )
    assert resp.status_code == 204, resp.text
    bad = runner.call(
        "POST",
        "/objective-report",
        json={
            "objective_id": obj["id"],
            "value": 1,
            "explanation": "x",
            "suggestions": [{"title": "x", "playbook": "remediate-dependency", "inputs": {}}],
        },
    )
    assert bad.status_code == 422
    use_jar(app, jars["erin"])
    [check] = app.get(f"/api/v1/objectives/{obj['id']}/history").json()
    assert check["value"] == 4 and check["status"] == "off_track" and len(check["suggestions"]) == 1
