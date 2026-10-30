"""draftPlan: deterministic parsing, playbook suggestion, and model-drafted plans validated against the catalog."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_llm import FakeLLM
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar


@pytest.fixture(scope="module")
def llm() -> Iterator[FakeLLM]:
    with FakeLLM() as fake:
        yield fake


@pytest.fixture
def keys() -> RecordingKeyAdmin:
    return RecordingKeyAdmin()


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], llm: FakeLLM, keys: RecordingKeyAdmin, oidc: FakeGitLabOIDC
) -> TestClient:
    llm.reset()
    client = make_client(llm_keys=keys, litellm_url=llm.url)
    seed_packs(client)
    jars = {u: sign_in_as(client, oidc, u) for u in ("alice", "bob")}
    use_jar(client, jars["alice"])  # expose the Jira writes so the planner may propose them
    ops = client.get("/api/v1/connectors/jira/operations").json()
    client.post(
        "/api/v1/connectors/jira/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(client),
    ).raise_for_status()
    use_jar(client, jars["bob"])
    return client


def draft(app: TestClient, prompt: str, **extra: Any) -> Any:
    return app.post("/api/v1/missions/draft-plan", json={"prompt": prompt, **extra}, headers=csrf(app))


def test_single_agent_request_needs_no_model(app: TestClient, llm: FakeLLM) -> None:
    resp = draft(app, "@vuln-analyst summarise the open findings of the payments service")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["parsed"]["agents"] == ["vuln-analyst"]
    [step] = body["plan"]["steps"]
    assert step["agent"] == "vuln-analyst" and step["prompt"].startswith("@vuln-analyst")
    assert body["estimated_tokens"] > 0
    assert llm.requests() == []


def test_unknown_mentions_are_rejected(app: TestClient) -> None:
    resp = draft(app, "@nobody do it")
    assert resp.status_code == 422 and "@nobody" in resp.json()["detail"]
    resp = draft(app, "check #notatool")
    assert resp.status_code == 422 and "#notatool" in resp.json()["detail"]


def test_model_plan_is_validated_and_repaired_once(app: TestClient, llm: FakeLLM, keys: RecordingKeyAdmin) -> None:
    bad = {
        "goal": "Triage",
        "steps": [
            {"id": "triage", "title": "Triage", "agent": "ghost", "prompt": "x"},
        ],
    }
    good = {
        "goal": "Triage and file tickets",
        "steps": [
            {"id": "Triage", "title": "Triage findings", "agent": "vuln-analyst", "prompt": "Triage the findings."},
            {
                "id": "file",
                "title": "File tickets",
                "agent": "ticket-manager",
                "prompt": "File one ticket per critical finding.",
                "depends_on": ["triage"],
                "writes": ["jira.issues.create"],
            },
        ],
    }
    llm.add_rules({"match": {"system_contains": "You plan work"}, "responses": [{"json": bad}, {"json": good}]})
    resp = draft(app, "triage this week's vulnerability findings and create jira tickets for the critical ones #jira")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["id"] for s in body["plan"]["steps"]] == ["triage", "file"]
    assert body["plan"]["writes"] == ["jira.issues.create"] and body["parsed"]["writes"] == ["jira.issues.create"]
    assert body["suggested_playbook"] == "weekly-vuln-triage"
    first, second = llm.requests()
    assert first["body"]["response_format"] == {"type": "json_object"}
    system = first["body"]["messages"][0]["content"]
    assert "vuln-analyst" in system and "jira.issues.create" in system
    assert "unknown agent 'ghost'" in second["body"]["messages"][-1]["content"]
    [(_, created)] = [c for c in keys.calls if c[0] == "create"]
    assert created["alias"] == "z-platform" and all(not m.startswith("ext-") for m in created["models"])
    # the platform key is reused, and the planner's tokens are counted for the user
    llm.add_rules({"match": {"system_contains": "You plan work"}, "responses": [{"json": good}]})
    assert draft(app, "triage and file tickets #jira").status_code == 200
    assert len([c for c in keys.calls if c[0] == "create"]) == 1
    usage = app.get("/api/v1/usage", params={"group_by": "agent", "period": "day"}).json()
    assert any(row.get("key") == "z:planner" for row in usage.get("rows", usage.get("series", []))), usage


def test_writes_that_are_not_exposed_are_refused(app: TestClient, llm: FakeLLM) -> None:
    plan = {
        "steps": [{"id": "x", "title": "x", "agent": "ticket-manager", "prompt": "x", "writes": ["jira.issues.delete"]}]
    }
    llm.add_rules({"match": {"system_contains": "You plan work"}, "responses": [{"json": plan}], "repeat": True})
    resp = draft(app, "delete the stale tickets and the other things")
    assert resp.status_code == 503 and "not available for writes" in resp.json()["detail"]


def test_gateway_down_is_unavailable(app: TestClient, llm: FakeLLM) -> None:
    llm.add_rules({"match": {}, "responses": [{"error": {"status": 500}}], "repeat": True})
    resp = draft(app, "plan something for the team")
    assert resp.status_code == 503 and "planner" in resp.json()["detail"]


def test_steps_are_ordered_and_cycles_refused(app: TestClient, llm: FakeLLM) -> None:
    out_of_order = {
        "steps": [
            {"id": "report", "agent": "ticket-manager", "prompt": "Report.", "depends_on": ["Scan"]},
            {"id": "scan", "agent": "vuln-analyst", "prompt": "Scan."},
        ]
    }
    cycle = {
        "steps": [
            {"id": "a", "agent": "vuln-analyst", "prompt": "A.", "depends_on": ["b"]},
            {"id": "b", "agent": "ticket-manager", "prompt": "B.", "depends_on": ["a"]},
        ]
    }
    llm.add_rules({"match": {"system_contains": "You plan work"}, "responses": [{"json": out_of_order}]})
    resp = draft(app, "scan the findings then report on them")
    assert resp.status_code == 200, resp.text
    assert [s["id"] for s in resp.json()["plan"]["steps"]] == ["scan", "report"]
    llm.add_rules({"match": {"system_contains": "You plan work"}, "responses": [{"json": cycle}], "repeat": True})
    resp = draft(app, "do a and b for the team")
    assert resp.status_code == 503 and "cycle" in resp.json()["detail"]


def test_mission_plans_must_be_ordered(app: TestClient) -> None:
    plan = {
        "steps": [
            {"id": "b", "agent": "vuln-analyst", "prompt": "B", "depends_on": ["a"]},
            {"id": "a", "agent": "vuln-analyst", "prompt": "A"},
        ]
    }
    resp = app.post("/api/v1/missions", json={"title": "x", "prompt": "x", "plan": plan}, headers=csrf(app))
    assert resp.status_code == 422 and "not earlier steps" in resp.text


def test_platform_key_failure_is_unavailable(make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC) -> None:
    from z_server.llm.litellm import LlmKeyError

    class Broken(RecordingKeyAdmin):
        async def create(self, *a: Any, **k: Any) -> Any:
            raise LlmKeyError("gateway down")

    client = make_client(llm_keys=Broken(), litellm_url="http://127.0.0.1:9")
    seed_packs(client)
    use_jar(client, sign_in_as(client, oidc, "bob"))
    resp = draft(client, "plan the week for the whole team")
    assert resp.status_code == 503 and "platform key" in resp.json()["detail"]
