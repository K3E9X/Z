"""Sessions: conversations that start missions (docs/10 §1)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

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
def app(make_client: Callable[..., TestClient], llm: FakeLLM) -> TestClient:
    llm.reset()
    client = make_client(llm_keys=RecordingKeyAdmin(), litellm_url=llm.url)
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("bob", "dave")}
    use_jar(app, jars["bob"])
    return jars


def new_session(app: TestClient) -> str:
    resp = app.post("/api/v1/sessions", json={"surface": "cli"}, headers=csrf(app))
    assert resp.status_code == 201, resp.text
    assert resp.json()["title"] == "New session" and resp.json()["missions"] == []
    return str(resp.json()["id"])


def say(app: TestClient, sid: str, text: str, **extra: object) -> dict[str, object]:
    resp = app.post(f"/api/v1/sessions/{sid}/messages", json={"text": text, **extra}, headers=csrf(app))
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


def test_a_message_to_one_agent_starts_a_mission(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    sid = new_session(app)
    reply = say(app, sid, "@vuln-analyst summarise the critical findings of payments", model="z-fast")
    mission = reply["mission"]
    assert isinstance(mission, dict) and mission["status"] in {"running", "queued"}
    assert mission["plan"]["steps"][0]["agent"] == "vuln-analyst" and mission["plan"]["steps"][0]["model"] == "z-fast"
    user, assistant = reply["messages"]  # type: ignore[misc]
    assert user["role"] == "user" and assistant["role"] == "assistant" and assistant["mission_id"] == mission["id"]
    assert "Started" in assistant["text"] and "@vuln-analyst" in assistant["text"]
    detail = app.get(f"/api/v1/sessions/{sid}").json()
    assert detail["title"].startswith("@vuln-analyst") and detail["missions"] == [mission["id"]]
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    listed = app.get("/api/v1/sessions", params={"surface": "cli"}).json()
    assert [s["id"] for s in listed["items"]] == [sid] and listed["next_cursor"] is None
    events = app.get("/api/v1/audit", params={"target_id": sid}).json()
    if "items" in events:
        assert {e["action"] for e in events["items"]} >= {"session.created", "session.message"}


def test_plan_first_waits_and_problems_become_replies(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    sid = new_session(app)
    reply = say(app, sid, "@vuln-analyst look at the open findings", plan_first=True)
    assert reply["mission"]["status"] == "planned"  # type: ignore[index]
    assert "Nothing runs until you accept it" in reply["messages"][1]["text"]  # type: ignore[index]
    reply = say(app, sid, "@nobody do the thing")
    assert reply.get("mission") is None and "@nobody" in reply["messages"][1]["text"]  # type: ignore[index]
    assert len(app.get(f"/api/v1/sessions/{sid}").json()["messages"]) == 4


def test_sessions_are_private(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    sid = new_session(app)
    use_jar(app, jars["dave"])
    assert app.get(f"/api/v1/sessions/{sid}").status_code == 404
    assert app.get("/api/v1/sessions").json()["items"] == []
    resp = app.post(f"/api/v1/sessions/{sid}/messages", json={"text": "hi"}, headers=csrf(app))
    assert resp.status_code in {403, 404}
    resp = app.post(
        "/api/v1/missions",
        json={"prompt": "x", "agents": ["vuln-analyst"], "session_id": sid, "start": False},
        headers=csrf(app),
    )
    assert resp.status_code in {403, 404}


def test_save_a_session_mission_as_playbook(
    app: TestClient, jars: dict[str, dict[str, str]], oidc: FakeGitLabOIDC
) -> None:
    sid = new_session(app)
    mission = say(app, sid, "@vuln-analyst summarise the critical findings of payments", plan_first=True)["mission"]
    assert isinstance(mission, dict)
    url = f"/api/v1/missions/{mission['id']}/save-as-playbook"
    resp = app.post(url, json={"name": "payments-findings", "domain": "vulnerabilities"}, headers=csrf(app))
    if resp.status_code == 403:  # bob may not write playbooks; an admin can, on any mission they can read
        use_jar(app, sign_in_as(app, oidc, "alice"))
        resp = app.post(url, json={"name": "payments-findings", "domain": "vulnerabilities"}, headers=csrf(app))
    assert resp.status_code == 201, resp.text
    pb = resp.json()
    assert pb["name"] == "payments-findings" and pb["version"] == "1.0.0"
    manifest = app.get(f"/api/v1/playbooks/{pb['id']}").json()
    steps = manifest.get("manifest", manifest).get("steps") or manifest.get("steps")
    assert steps[0]["agent"] == "vuln-analyst" and steps[0]["prompt"].startswith("@vuln-analyst")
    again = app.post(url, json={"name": "payments-findings"}, headers=csrf(app))
    assert again.status_code == 409
    run = app.post(f"/api/v1/playbooks/{pb['id']}/run", json={"inputs": {}, "plan_first": True}, headers=csrf(app))
    assert run.status_code == 201, run.text


def test_take_over_needs_a_job_that_runs(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    sid = new_session(app)
    mission = say(app, sid, "@vuln-analyst summarise the critical findings")["mission"]
    assert isinstance(mission, dict)
    resp = app.post(f"/api/v1/missions/{mission['id']}/take-over", json={"job_id": "job_nope"}, headers=csrf(app))
    assert resp.status_code == 404
    job = app.get(f"/api/v1/missions/{mission['id']}/jobs").json()
    jobs = job.get("items", job) if isinstance(job, dict) else job
    if jobs:
        resp = app.post(
            f"/api/v1/missions/{mission['id']}/take-over", json={"job_id": jobs[0]["id"]}, headers=csrf(app)
        )
        assert resp.status_code == 409 and "only a running or paused job" in resp.json()["detail"]
