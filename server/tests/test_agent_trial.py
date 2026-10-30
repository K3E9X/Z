"""testAgent: a read-only trial mission with one agent, also a disabled one (the agent builder tries a new agent
before it is offered to anyone), while a disabled agent stays unusable for every other mission."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

AGENT = "ticket-manager"


@pytest.fixture
def app(make_client: Callable[..., TestClient], tmp_path: Any) -> TestClient:
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "artifacts")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    return {u: sign_in_as(app, oidc, u) for u in ("alice", "bob")}


def as_user(app: TestClient, jars: dict[str, dict[str, str]], user: str) -> dict[str, str]:
    use_jar(app, jars[user])
    return csrf(app)


def set_status(app: TestClient, name: str, status: str) -> None:
    from z_server.db.models import Agent

    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> None:
        async with state.sessions() as db, db.begin():
            await db.execute(update(Agent).where(Agent.name == name).values(status=status))

    app.portal.call(go)  # type: ignore[union-attr]


def test_a_disabled_agent_can_be_tried_read_only(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    set_status(app, AGENT, "disabled")
    h = as_user(app, jars, "alice")
    plan = {"steps": [{"id": "main", "agent": AGENT, "prompt": "Triage"}]}
    normal = app.post("/api/v1/missions", json={"title": "Triage", "prompt": "Triage", "plan": plan}, headers=h)
    assert normal.status_code == 422 and "disabled" in normal.json()["detail"]
    draft = app.post(
        "/api/v1/missions", json={"title": "Triage", "prompt": "x", "plan": plan, "start": False}, headers=h
    )
    assert draft.status_code == 422

    agent = app.get(f"/api/v1/agents/{AGENT}").json()
    tried = app.post(
        f"/api/v1/agents/{agent['id']}/test",
        json={"prompt": "Find the open critical tickets", "inputs": {"project": "SECOPS"}},
        headers=h,
    )
    assert tried.status_code == 201, tried.text
    mission = tried.json()
    assert mission["title"].startswith("Trial: ") and mission["status"] == "queued"
    assert mission["autonomy"] == "read_only" and mission["connectors"] == [] and mission["data_class"] == "C2"
    assert mission["plan"]["steps"][0]["agent"] == AGENT and mission["plan"]["writes"] == []
    assert mission["plan"]["steps"][0]["writes"] == [] and mission["inputs"] == {"project": "SECOPS"}
    assert mission["budget"]["max_tokens"] <= 200_000 and mission["budget"]["timeout_s"] <= 900

    # the job runs with no way to write: no write targets, no propose_action
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    spec = runner.get_spec()
    assert spec["agent"]["name"] == AGENT and spec["prompt"] == "Find the open critical tickets"
    assert spec["writes"] == [] and "propose_action" not in spec["tools"]["builtins"]
    assert all(t["operation"] in {"jira.issues.search", "jira.issues.get"} for t in spec["tools"]["gateway"])

    # the trial did not enable the agent: normal missions still refuse it
    assert app.get(f"/api/v1/agents/{AGENT}").json()["status"] == "disabled"
    again = app.post("/api/v1/missions", json={"title": "Triage", "prompt": "Triage", "plan": plan}, headers=h)
    assert again.status_code == 422
    audit = app.get("/api/v1/audit", params={"action": "agent.tested"}).json()["items"]
    assert audit and audit[0]["target_id"] == agent["id"] and audit[0]["after"]["mission"] == mission["id"]


def test_trial_needs_agents_write_and_a_prompt(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    h = as_user(app, jars, "bob")  # operator: can start missions, cannot build agents
    assert app.post(f"/api/v1/agents/{AGENT}/test", json={"prompt": "x"}, headers=h).status_code == 403
    h = as_user(app, jars, "alice")
    assert app.post(f"/api/v1/agents/{AGENT}/test", json={"prompt": ""}, headers=h).status_code == 422
    assert app.post(f"/api/v1/agents/{AGENT}/test", json={"prompt": "x", "writes": ["a"]}, headers=h).status_code == 422
    assert app.post("/api/v1/agents/nobody/test", json={"prompt": "x"}, headers=h).status_code == 404
    ok = app.post(f"/api/v1/agents/{AGENT}/test", json={"prompt": "Hello"}, headers=h)  # an enabled agent too
    assert ok.status_code == 201 and ok.json()["title"].startswith("Trial: ")


def test_the_trial_exception_is_only_for_trials() -> None:
    from z_common.models.enums import Autonomy
    from z_common.models.manifests import MissionPlan, PlanStep
    from z_server.errors import Problem
    from z_server.missions import service

    def request(**kw: Any) -> service.MissionRequest:
        base: dict[str, Any] = {
            "title": "Trial: x",
            "prompt": "x",
            "playbook": None,
            "inputs": {},
            "plan": MissionPlan(steps=[PlanStep(id="trial", agent="a", prompt="x")]),
            "connectors": [],
            "data_class": None,
            "autonomy": Autonomy.read_only,
            "plan_first": False,
            "budget": {},
            "session_id": None,
            "trial_agent": "a",
        }
        return service.MissionRequest(**{**base, **kw})

    service._check_trial(request(), request().plan)  # type: ignore[arg-type]
    bad = [
        request(autonomy=Autonomy.propose),
        request(connectors=["jira"]),
        request(plan=MissionPlan(steps=[PlanStep(id="t", agent="a", prompt="x", writes=["jira.issues.create"])])),
        request(plan=MissionPlan(steps=[PlanStep(id="t", agent="b", prompt="x")])),
        request(
            plan=MissionPlan(steps=[PlanStep(id="t", agent="a", prompt="x"), PlanStep(id="u", agent="a", prompt="y")])
        ),
    ]
    for item in bad:
        with pytest.raises(Problem):
            service._check_trial(item, item.plan)  # type: ignore[arg-type]
