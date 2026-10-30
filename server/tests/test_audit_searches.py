"""Saved audit log searches (/me/audit-searches): per user, unique names, at most 50, audited."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import csrf, sign_in_as, use_jar

URL = "/api/v1/me/audit-searches"


@pytest.fixture
def app(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    return {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "dave")}


def as_user(app: TestClient, jars: dict[str, dict[str, str]], user: str) -> dict[str, str]:
    use_jar(app, jars[user])
    return csrf(app)


def test_saved_audit_searches_are_per_user(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    h = as_user(app, jars, "dave")
    assert app.get(URL).json() == []
    filters = {"action": "connector.", "result": "denied", "from": "2026-10-01T00:00:00Z"}
    created = app.post(URL, json={"name": "Denied connector calls", "filters": filters}, headers=h)
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["id"] and saved["name"] == "Denied connector calls" and saved["filters"]["action"] == "connector."
    assert saved["filters"]["from"].startswith("2026-10-01")
    assert app.post(URL, json={"name": "denied connector calls", "filters": {}}, headers=h).status_code == 409
    assert app.post(URL, json={"name": "x", "filters": {"bogus": 1}}, headers=h).status_code == 422
    assert app.post(URL, json={"name": "", "filters": {}}, headers=h).status_code == 422
    assert app.get(URL).json() == [saved]

    # another user has their own list (and the same name is fine there)
    h_alice = as_user(app, jars, "alice")
    assert app.get(URL).json() == []
    assert app.post(URL, json={"name": "Denied connector calls", "filters": {}}, headers=h_alice).status_code == 201
    assert app.delete(f"{URL}/{saved['id']}", headers=h_alice).status_code == 404

    h = as_user(app, jars, "dave")
    for n in range(49):
        assert app.post(URL, json={"name": f"s{n}", "filters": {}}, headers=h).status_code == 201
    full = app.post(URL, json={"name": "one more", "filters": {}}, headers=h)
    assert full.status_code == 409 and "50" in full.json()["detail"]
    assert app.delete(f"{URL}/{saved['id']}", headers=h).status_code == 204
    assert saved["id"] not in {s["id"] for s in app.get(URL).json()}
    assert len(app.get(URL).json()) == 49
    audit = app.get("/api/v1/audit", params={"action": "audit.search", "limit": 200}).json()["items"]
    actions = [e["action"] for e in audit]
    assert actions[0] == "audit.search_deleted" and actions.count("audit.search_saved") >= 50

    # audit:read is needed (an operator only reads their own audit trail)
    as_user(app, jars, "bob")
    assert app.get(URL).status_code == 403
