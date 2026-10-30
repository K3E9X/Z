"""Agent builder: the drafted profile is made safe whatever the model answers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_llm import FakeLLM
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

BUILDER = {"system_contains": "You design AI agents"}


@pytest.fixture(scope="module")
def llm() -> Iterator[FakeLLM]:
    with FakeLLM() as fake:
        yield fake


@pytest.fixture
def app(make_client: Callable[..., TestClient], llm: FakeLLM, oidc: FakeGitLabOIDC) -> TestClient:
    llm.reset()
    client = make_client(llm_keys=RecordingKeyAdmin(), litellm_url=llm.url)
    seed_packs(client)
    use_jar(client, sign_in_as(client, oidc, "alice"))
    ops = client.get("/api/v1/connectors/jira/operations").json()
    client.post(
        "/api/v1/connectors/jira/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(client),
    ).raise_for_status()
    return client


def draft(app: TestClient, description: str) -> Any:
    return app.post("/api/v1/agents/draft", json={"description": description}, headers=csrf(app))


def test_draft_is_sanitized(app: TestClient, llm: FakeLLM) -> None:
    answer = {
        "profile": {
            "name": "Ticket Manager",
            "display_name": "Backlog keeper",
            "handle": "ticket-manager",
            "role": "Keeps the SECOPS backlog clean.",
            "instructions": "Merge duplicates, fill owners.",
            "model": "ext-gpt-ultra",
            "skills": ["jira-dedup", "telepathy"],
            "tools": [
                "read",
                "gateway:jira.issues.search",
                "gateway:jira.issues.create",
                "gateway:jira.issues.delete",
                "shell",
            ],
            "autonomy": "auto",
            "limits": {"max_steps": 10_000, "max_tokens": 50, "timeout_s": "soon"},
        },
        "questions": ["Which Jira project?", "Which team owns it?", "A third question"],
        "objectives": [
            {
                "title": "No stale tickets",
                "metric": {"source": "z", "query": "stale_tickets", "target": 0, "direction": "down"},
                "cadence": "0 6 * * 1",
            },
            {"title": "broken"},
        ],
        "new_skills": [{"name": "Backlog Triage", "purpose": "Sort the backlog"}],
    }
    llm.add_rules({"match": BUILDER, "responses": [{"json": answer}]})
    resp = draft(app, "an agent that keeps our Jira SECOPS backlog clean")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    profile = body["profile"]
    assert profile["name"] == "ticket-manager-2" and profile["handle"] == "ticket-manager-2"  # not taken twice
    assert profile["tools"] == ["read", "gateway:jira.issues.search"]  # writes and unknown tools dropped
    assert (
        profile["skills"] == ["jira-dedup"]
        and {"name": "telepathy", "purpose": "Requested by the draft"} in body["new_skills"]
    )
    assert profile["autonomy"] == "propose" and profile["model"] == "z-heavy"
    assert profile["limits"] == {"max_steps": 120, "max_tokens": 50, "timeout_s": 1800}
    assert len(body["questions"]) == 2 and [o["title"] for o in body["objectives"]] == ["No stale tickets"]
    created = app.post("/api/v1/agents", json=profile, headers=csrf(app))
    assert created.status_code == 201, created.text


def test_unavailable_model(app: TestClient, llm: FakeLLM) -> None:
    llm.add_rules({"match": {}, "responses": [{"error": {"status": 500}}], "repeat": True})
    resp = draft(app, "an agent")
    assert resp.status_code == 503 and "agent builder" in resp.json()["detail"]
