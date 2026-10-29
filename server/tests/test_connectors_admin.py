"""Connector administration: create from a spec, validation of credentials, test and health, edit, import."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.server import csrf, seed_packs, sign_in_as


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def admin(
    make_client: Callable[..., TestClient],
    oidc: FakeGitLabOIDC,
    tools: FakeTools,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> TestClient:
    tools.reset()
    env = {
        "JIRA_URL": tools.url_for("jira"),
        "JIRA_READ_EMAIL": "z-read@example.test",
        "JIRA_READ_TOKEN": tools.token("jira", "read"),
        "JIRA_WRITE_EMAIL": "z-write@example.test",
        "JIRA_WRITE_TOKEN": tools.token("jira", "write"),
        "DOJO_RO": tools.token("defectdojo", "read"),
        "DOJO_RW": tools.token("defectdojo", "write"),
        "GITLAB_TOKEN_RO": tools.token("gitlab", "read"),
        "GITLAB_TOKEN_RW": tools.token("gitlab", "write"),
        "GLDRAFT_TOKEN_RO": tools.token("gitlab", "read"),
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    client = make_client(artifacts_dir=tmp_path / "a")
    seed_packs(client)
    sign_in_as(client, oidc, "alice")
    return client


def dojo(tools: FakeTools, **extra: Any) -> dict[str, Any]:
    return {
        "name": "dojolab",
        "kind": "openapi",
        "display_name": "DefectDojo lab",
        "base_url": tools.url_for("defectdojo"),
        "spec": f"{tools.url_for('defectdojo')}/api/v2/oa3/schema/?format=json",
        "operation_ids": "from_path",
        "auth": {
            "read": {"type": "api_key", "header": "Authorization", "prefix": "Token", "secret": "env:DOJO_RO"},
            "write": {"type": "api_key", "header": "Authorization", "prefix": "Token", "secret": "env:DOJO_RW"},
        },
        "health": {"path": "/api/v2/products/"},
        "data_class": "C2",
        **extra,
    }


def test_create_from_a_remote_openapi_spec(admin: TestClient, tools: FakeTools) -> None:
    created = admin.post("/api/v1/connectors", json=dojo(tools), headers=csrf(admin))
    assert created.status_code == 201, created.text
    body = created.json()
    assert (
        body["review_status"] == "pending_review" and body["operations_total"] >= 4 and body["operations_exposed"] == 0
    )
    assert {c["purpose"] for c in body["credentials"]} == {"read", "write"} and all(
        c["ref"].startswith("env:") for c in body["credentials"]
    )
    ops = {o["id"]: o for o in admin.get("/api/v1/connectors/dojolab/operations").json()}
    assert "dojolab.findings.list" in ops and ops["dojolab.findings.list"]["class"] == "read"
    assert admin.post("/api/v1/connectors", json=dojo(tools), headers=csrf(admin)).status_code == 409


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda m: m["auth"]["read"].update(secret="hunter2-plain-token"), ""),
        (lambda m: m["auth"]["write"].update(secret="env:DOJO_RO"), "invariant 7"),
        (lambda m: m.update(base_url="http://169.254.169.254/latest"), "metadata"),
    ],
    ids=["secret value", "same read and write", "metadata address"],
)
def test_create_refuses_unsafe_manifests(admin: TestClient, tools: FakeTools, mutate: Any, message: str) -> None:
    manifest = dojo(tools)
    mutate(manifest)
    resp = admin.post("/api/v1/connectors", json=manifest, headers=csrf(admin))
    assert resp.status_code == 422, resp.text
    assert message in resp.text
    assert "hunter2-plain-token" not in resp.text


def test_test_connection_and_health(admin: TestClient) -> None:
    from z_server.workers import health

    ops = admin.get("/api/v1/connectors/jira/operations").json()
    admin.post(
        "/api/v1/connectors/jira/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(admin),
    )
    probe = admin.post("/api/v1/connectors/jira/test", json={}, headers=csrf(admin)).json()
    assert probe["ok"] is True and probe["status_code"] == 200 and probe["sample"]["displayName"] == "Z Service"
    op = admin.post(
        "/api/v1/connectors/jira/test",
        json={"operation": "issues.search", "params": {"jql": "project = SECOPS", "maxResults": 1}},
        headers=csrf(admin),
    ).json()
    assert op["ok"] is True and len(op["sample"]["issues"]) == 1
    write = admin.post("/api/v1/connectors/jira/test", json={"operation": "issues.create"}, headers=csrf(admin))
    assert write.status_code == 422  # never a write to test
    connector = admin.get("/api/v1/connectors/jira").json()
    assert connector["health"]["status"] == "healthy" and connector["health"]["checked_at"]
    state = admin.app.state.z  # type: ignore[attr-defined]
    results = admin.portal.call(health.check_all, state)  # type: ignore[union-attr]
    assert results == {} or results.get("jira") == "healthy"  # just checked: not due yet


def test_edit_that_raises_a_class_resets_exposure(admin: TestClient, tools: FakeTools) -> None:
    admin.post("/api/v1/connectors", json=dojo(tools), headers=csrf(admin))
    admin.post(
        "/api/v1/connectors/dojolab/approve-exposure",
        json={"operations": [{"id": "dojolab.findings.get", "exposed": True}]},
        headers=csrf(admin),
    )
    manifest = dojo(tools, operations={"rules": [{"match": "findings.get", "class": "write"}]})
    patched = admin.patch("/api/v1/connectors/dojolab", json={"manifest": manifest}, headers=csrf(admin))
    assert patched.status_code == 200, patched.text
    assert patched.json()["review_status"] == "pending_review"
    op = next(o for o in admin.get("/api/v1/connectors/dojolab/operations").json() if o["id"] == "dojolab.findings.get")
    assert op["class"] == "write" and op["exposed"] is False
    renamed = admin.patch(
        "/api/v1/connectors/dojolab", json={"manifest": {**manifest, "kind": "rest"}}, headers=csrf(admin)
    )
    assert renamed.status_code == 422
    disabled = admin.patch("/api/v1/connectors/dojolab", json={"enabled": False}, headers=csrf(admin))
    assert disabled.json()["enabled"] is False


def test_import_drafts_from_openapi_and_mcp(admin: TestClient, tools: FakeTools) -> None:
    draft = admin.post(
        "/api/v1/connectors/import",
        json={
            "source": "openapi",
            "name": "dojodraft",
            "spec_url": f"{tools.url_for('defectdojo')}/api/v2/oa3/schema/?format=json",
            "base_url": tools.url_for("defectdojo"),
        },
        headers=csrf(admin),
    )
    assert draft.status_code == 200, draft.text
    d = draft.json()
    assert (
        d["manifest"]["kind"] == "openapi" and d["operations"] and all(o["exposed"] is False for o in d["operations"])
    )
    mcp = admin.post(
        "/api/v1/connectors/import",
        json={"source": "mcp", "name": "gldraft", "mcp": {"transport": "http", "url": tools.mcp_url("gitlab")}},
        headers=csrf(admin),
    )
    assert mcp.status_code == 200, mcp.text
    m = mcp.json()
    by_id = {o["id"]: o for o in m["operations"]}
    assert (
        "gldraft.tools.merge_merge_request" in by_id
        and by_id["gldraft.tools.merge_merge_request"]["class"] == "destructive"
    )
    assert by_id["gldraft.tools.list_projects"]["class"] == "read"
    assert any("destructive" in w for w in m["warnings"])
    docs = admin.post(
        "/api/v1/connectors/import", json={"source": "docs", "name": "x", "docs": "..."}, headers=csrf(admin)
    )
    assert docs.status_code == 503
