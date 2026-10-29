"""B6: routing policy and rules, learning signals, eval runs graded on real missions, and learner proposals
evaluated before/after with gates, applied as new versions."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_llm import FakeLLM
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import base_config, csrf, seed_packs, sign_in_as, use_jar

GOOD = {
    "title": "Release 2.4 notes",
    "bullets": ["Single sign-on with the corporate IdP", "Password login removed"],
    "source": "notes.md",
}
BAD = {"title": "", "bullets": [], "source": "elsewhere.md"}
RUBRIC = {"system_contains": "You grade the work"}
PLANNER = {"system_contains": "You improve an AI agent"}


@pytest.fixture(scope="module")
def llm() -> Iterator[FakeLLM]:
    with FakeLLM(embed_dim=1024) as fake:
        yield fake


@pytest.fixture
def app(make_client: Callable[..., TestClient], llm: FakeLLM, oidc: FakeGitLabOIDC, tmp_path: Any) -> TestClient:
    llm.reset()
    config = base_config(oidc.issuer)
    config["learning"]["run_as"] = "alice"
    config["learning"]["min_signals"] = 2
    client = make_client(config=config, llm_keys=RecordingKeyAdmin(), litellm_url=llm.url, artifacts_dir=tmp_path / "a")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}
    use_jar(app, jars["alice"])
    return jars


def state(app: TestClient) -> Any:
    return app.app.state.z  # type: ignore[attr-defined]


def call(app: TestClient, fn: Any, *args: Any) -> Any:
    return app.portal.call(fn, state(app), *args)  # type: ignore[union-attr]


def play_all(app: TestClient, outputs: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    """Run every queued job to completion with the output chosen from its spec."""
    executor = RecordingExecutor()
    specs = []
    for job_id in dispatch(app, executor):
        runner = ScriptedRunner.for_job(app, executor, job_id)
        spec = runner.get_spec()
        specs.append(spec)
        assert runner.complete(output=outputs(spec)).status_code == 200
    return specs


# ---------------------------------------------------------------- routing policy


def test_routing_policy_floor_and_rules(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    policy = app.get("/api/v1/routing-policy").json()
    assert {"C2", "C3"} <= set(policy["never_external"]) and policy["rules"] == []
    body = {k: policy[k] for k in ("default_mission_class", "external_fallback_when_degraded", "never_in_memory")}
    refused = app.put("/api/v1/routing-policy", json={**body, "never_external": ["C3"]}, headers=csrf(app))
    assert refused.status_code == 422 and "C2" in refused.json()["detail"]
    rule = {
        "id": "new",
        "match": {"finding_kind": "dependency"},
        "target": {"agent": "dev-engineer"},
        "origin": "config",
        "version": 1,
    }
    saved = app.put(
        "/api/v1/routing-policy", json={**body, "never_external": ["C2", "C3"], "rules": [rule]}, headers=csrf(app)
    )
    assert saved.status_code == 200, saved.text
    [stored] = saved.json()["rules"]
    assert stored["target"] == {"agent": "dev-engineer"} and stored["version"] == 1
    edited = {**stored, "match": {"finding_kind": ["dependency", "sca"]}}
    again = app.put(
        "/api/v1/routing-policy", json={**body, "never_external": ["C2", "C3"], "rules": [edited]}, headers=csrf(app)
    )
    assert again.json()["rules"][0]["version"] == 2

    from z_server.llm import policy as routing

    async def route(st: Any, event: dict[str, Any]) -> Any:
        async with st.sessions() as db:
            return await routing.route(db, event)

    assert call(app, route, {"finding_kind": "SCA"}) == ("dev-engineer", None)
    assert call(app, route, {"finding_kind": "secret"}) == (None, None)
    cleared = app.put(
        "/api/v1/routing-policy", json={**body, "never_external": ["C2", "C3"], "rules": []}, headers=csrf(app)
    )
    assert cleared.json()["rules"] == []
    use_jar(app, jars["bob"])
    assert (
        app.put("/api/v1/routing-policy", json={**body, "never_external": ["C2", "C3"]}, headers=csrf(app)).status_code
        == 403
    )


# ---------------------------------------------------------------- evals


def test_eval_run_grades_real_missions(app: TestClient, llm: FakeLLM, jars: dict[str, dict[str, str]]) -> None:
    llm.add_rules({"match": RUBRIC, "responses": [{"json": {"score": 2, "justification": "fine"}}], "repeat": True})
    started = app.post(
        "/api/v1/evals",
        json={"target": "agent:summarizer", "models": ["z-fast"], "cases": ["sum-1", "sum-2"]},
        headers=csrf(app),
    )
    assert started.status_code == 202, started.text
    [run] = started.json()
    assert run["status"] == "running" and run["model"] == "z-fast" and len(run["cases"]) == 2
    assert app.get("/api/v1/missions").json()["items"] == []  # eval missions are not listed with real work
    specs = play_all(app, lambda spec: GOOD if "notes.md" in str(spec) else BAD)
    assert len(specs) == 2 and all(s["model"] == "z-fast" for s in specs)
    assert all(s["workspace"]["mode"] == "files" for s in specs)

    from z_server import evals

    assert call(app, evals.tick) == [run["id"]]
    graded = app.get(f"/api/v1/evals/{run['id']}").json()
    assert graded["status"] == "succeeded", graded
    scores = graded["scores"]
    by_case = {c["id"]: c for c in graded["cases"]}
    assert by_case["sum-1"]["passed"] is True, by_case["sum-1"]
    assert by_case["sum-2"]["passed"] is True and by_case["sum-2"]["rubric"][0]["score"] == 2
    assert scores["cases_passed"] == 2 and scores["rubric_avg"] == 2.0 and graded["passed"] is True
    listed = app.get("/api/v1/evals", params={"target": "agent:summarizer"}).json()["items"]
    assert [r["id"] for r in listed] == [run["id"]]


def test_failing_checks_fail_the_case(app: TestClient, llm: FakeLLM, jars: dict[str, dict[str, str]]) -> None:
    llm.add_rules({"match": RUBRIC, "responses": [{"json": {"score": 0, "justification": "invented"}}], "repeat": True})
    [run] = app.post("/api/v1/evals", json={"target": "agent:summarizer", "cases": ["sum-1"]}, headers=csrf(app)).json()
    assert run["model"] == "z-fast"  # the agent's own model by default
    play_all(app, lambda spec: BAD)
    from z_server import evals

    call(app, evals.tick)
    [case] = app.get(f"/api/v1/evals/{run['id']}").json()["cases"]
    failed = {c["type"] for c in case["checks"] if not c["ok"]}
    assert case["passed"] is False and {"json_schema", "field_equals"} <= failed
    unknown = app.post("/api/v1/evals", json={"target": "agent:summarizer", "cases": ["nope"]}, headers=csrf(app))
    assert unknown.status_code == 422


# ---------------------------------------------------------------- signals and proposals


def reject_twice(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    """Two rejected Jira writes by the ticket manager: signals for the learner."""
    from z_server.db.models import Signal
    from z_server.ids import new_id

    async def add(st: Any) -> None:
        async with st.sessions() as db, db.begin():
            for i in range(2):
                db.add(
                    Signal(
                        id=new_id("sig"),
                        kind="action_rejected",
                        agent="summarizer",
                        payload={"reason": f"the summary invented facts ({i})"},
                    )
                )

    call(app, add)


SKILL_EDIT = {
    "kind": "skill_edit",
    "target": "summarize",
    "title": "Quote the file name as source",
    "rationale": "Two summaries were rejected for invented facts.",
    "skill_content": "---\nname: summarize\ndescription: Summarize one file.\n---\nOnly state facts that appear in the file.\n",
}
FACT = {
    "kind": "memory_fact",
    "title": "Release notes live in notes.md",
    "fact": "Release notes are in notes.md.",
    "scope": "agent",
}
RULE = {
    "kind": "routing_rule",
    "title": "Release notes go to the summarizer",
    "match": {"kind": "release_notes"},
    "agent": "summarizer",
}


def test_learner_proposes_evaluates_and_applies(app: TestClient, llm: FakeLLM, jars: dict[str, dict[str, str]]) -> None:
    from z_server import learner
    from z_server.workers import learning

    signals = app.get("/api/v1/signals", params={"agent": "summarizer"}).json()["items"]
    assert signals == []
    reject_twice(app, jars)
    assert len(app.get("/api/v1/signals", params={"kind": "action_rejected"}).json()["items"]) == 2
    llm.add_rules(
        {"match": PLANNER, "responses": [{"json": {"proposals": [SKILL_EDIT, FACT, RULE, {"kind": "nonsense"}]}}]}
    )
    llm.add_rules({"match": RUBRIC, "responses": [{"json": {"score": 2, "justification": "ok"}}], "repeat": True})
    created = call(app, learner.observe)
    assert len(created) == 3
    proposals = {p["kind"]: p for p in app.get("/api/v1/proposals").json()["items"]}
    skill = proposals["skill_edit"]
    assert skill["status"] == "evaluating" and "+Only state facts" in skill["diff"] and len(skill["evidence"]) == 2
    assert proposals["memory_fact"]["status"] == "ready" and proposals["routing_rule"]["status"] == "ready"
    assert call(app, learner.observe) == []  # the signals are used now

    # both eval runs (current and candidate) run as missions; the candidate's carry the new skill text
    specs = play_all(app, lambda spec: GOOD)
    contents = {s["skills"][0]["content"] for s in specs}
    assert SKILL_EDIT["skill_content"] in contents and len(contents) == 2
    result = call(app, learning.tick)
    assert len(result["graded"]) == 2 and result["decided"] == [skill["id"]]
    ready = app.get(f"/api/v1/proposals/{skill['id']}").json()
    assert ready["status"] == "ready" and ready["regressions"] == [] and ready["eval_after"]["cases_total"] == 5

    applied = app.post(f"/api/v1/proposals/{skill['id']}/apply", json={}, headers=csrf(app))
    assert applied.status_code == 200 and applied.json()["status"] == "applied", applied.text
    current = app.get("/api/v1/skills/summarize").json()
    assert current["version"] == "1.1.0", current
    assert app.post(f"/api/v1/proposals/{skill['id']}/apply", json={}, headers=csrf(app)).status_code == 409

    rule = app.post(f"/api/v1/proposals/{proposals['routing_rule']['id']}/apply", json={}, headers=csrf(app)).json()
    assert rule["status"] == "applied"
    rules = app.get("/api/v1/routing-policy").json()["rules"]
    assert rules[0]["origin"] == "learned" and rules[0]["target"] == {"agent": "summarizer"}
    fact = app.post(f"/api/v1/proposals/{proposals['memory_fact']['id']}/apply", json={}, headers=csrf(app))
    assert fact.status_code == 200, fact.text
    use_jar(app, jars["bob"])  # an operator cannot apply learning
    assert app.post(f"/api/v1/proposals/{skill['id']}/reject", json={}, headers=csrf(app)).status_code == 403


def test_regression_discards_and_try_runs_the_candidate(
    app: TestClient, llm: FakeLLM, jars: dict[str, dict[str, str]]
) -> None:
    from z_server import learner
    from z_server.workers import learning

    reject_twice(app, jars)
    llm.add_rules({"match": PLANNER, "responses": [{"json": {"proposals": [SKILL_EDIT]}}]})
    llm.add_rules({"match": RUBRIC, "responses": [{"json": {"score": 2, "justification": "ok"}}], "repeat": True})
    [pid] = call(app, learner.observe)
    # the candidate breaks every case: its missions answer badly
    play_all(app, lambda spec: BAD if spec["skills"][0]["content"] == SKILL_EDIT["skill_content"] else GOOD)
    call(app, learning.tick)
    discarded = app.get(f"/api/v1/proposals/{pid}").json()
    assert discarded["status"] == "discarded" and "sum-1" in discarded["regressions"]
    assert app.post(f"/api/v1/proposals/{pid}/apply", json={}, headers=csrf(app)).status_code == 409

    reject_twice(app, jars)
    llm.add_rules({"match": PLANNER, "responses": [{"json": {"proposals": [SKILL_EDIT]}}]})
    [pid2] = call(app, learner.observe)
    tried = app.post(f"/api/v1/proposals/{pid2}/try", json={}, headers=csrf(app))
    assert tried.status_code == 200, tried.text
    executor = RecordingExecutor()
    jobs = dispatch(app, executor)
    specs = [ScriptedRunner.for_job(app, executor, j).get_spec() for j in jobs]
    assert any(
        s["mission_id"] == tried.json()["id"] and s["skills"][0]["content"] == SKILL_EDIT["skill_content"]
        for s in specs
    )
    rejected = app.post(f"/api/v1/proposals/{pid2}/reject", json={"reason": "not now"}, headers=csrf(app)).json()
    assert rejected["status"] == "rejected" and rejected["decided_by"] == "alice"


def test_eval_missions_capture_writes_and_answer_questions(
    app: TestClient, llm: FakeLLM, jars: dict[str, dict[str, str]]
) -> None:
    ops = app.get("/api/v1/connectors/jira/operations").json()
    app.post(
        "/api/v1/connectors/jira/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(app),
    ).raise_for_status()
    first = app.get("/api/v1/agents/ticket-manager").json()
    assert first["name"] == "ticket-manager"
    started = app.post("/api/v1/evals", json={"target": "agent:ticket-manager"}, headers=csrf(app))
    assert started.status_code == 202, started.text
    executor = RecordingExecutor()
    job_id = dispatch(app, executor)[0]
    runner = ScriptedRunner.for_job(app, executor, job_id)
    spec = runner.get_spec()
    assert "jira.issues.create" in {w["operation"] for w in spec["writes"]}
    proposed = runner.call(
        "POST",
        "/actions",
        json={
            "connector": "jira",
            "operation": "jira.issues.create",
            "summary": "file it",
            "payload": {"project": "SECOPS", "summary": "Eval ticket", "description": "Created by an eval case run."},
        },
    )
    assert proposed.status_code == 201, proposed.text
    assert proposed.json()["status"] == "rejected" and proposed.json()["result"]["captured"] is True
    asked = runner.call("POST", "/questions", json={"text": "Which project?", "options": ["SECOPS", "PAY"]})
    assert asked.status_code == 201 and asked.json()["answer"] in {"SECOPS", "PAY"}
    assert runner.control()["state"] == "run"
    assert app.get("/api/v1/review").json()["counts"]["all"] == 0  # nothing waits for a human
    assert app.get("/api/v1/signals").json()["items"] == []  # eval runs teach the learner nothing


def test_signals_come_from_review_and_guardrails(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    ops = app.get("/api/v1/connectors/jira/operations").json()
    app.post(
        "/api/v1/connectors/jira/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(app),
    ).raise_for_status()
    use_jar(app, jars["bob"])
    writes = ["jira.issues.create"]
    plan = {"steps": [{"id": "act", "agent": "ticket-manager", "prompt": "File", "writes": writes}], "writes": writes}
    created = app.post("/api/v1/missions", json={"title": "T", "prompt": "T", "plan": plan}, headers=csrf(app))
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    payload = {"project": "SECOPS", "summary": "A ticket", "description": "A ticket with enough words in it."}
    action = runner.call(
        "POST",
        "/actions",
        json={"connector": "jira", "operation": "jira.issues.create", "summary": "x", "payload": payload},
    ).json()
    denied = runner.call(
        "POST",
        "/actions",
        json={"connector": "jira", "operation": "jira.comments.create", "summary": "x", "payload": {}},
    )
    assert denied.status_code == 403
    use_jar(app, jars["carol"])
    edited = app.post(
        f"/api/v1/actions/{action['id']}/edit",
        json={"payload": {**payload, "summary": "A better ticket"}},
        headers=csrf(app),
    ).json()
    assert (
        app.post(f"/api/v1/actions/{edited['id']}/reject", json={"reason": "wrong team"}, headers=csrf(app)).status_code
        == 200
    )
    use_jar(app, jars["alice"])
    kinds = {s["kind"]: s for s in app.get("/api/v1/signals", params={"agent": "ticket-manager"}).json()["items"]}
    assert {"guardrail_hit", "action_edited", "action_rejected"} <= set(kinds), kinds
    assert kinds["action_edited"]["payload"]["after"]["summary"] == "A better ticket"
