"""Typed connector secrets (ADR 0033): an admin types a connector's read or write secret in the web app; it is
sealed with the platform key, used by the gateway (read) and the action executor (write) only, never shown, and
clearing it goes back to the manifest reference."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

ISSUE = {"project": "SECOPS", "summary": "Patch openssl", "description": "CVE", "labels": ["vuln"], "priority": "High"}
SEARCH = {"jql": "project = SECOPS", "maxResults": 1}


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def env(tools: FakeTools, monkeypatch: pytest.MonkeyPatch) -> FakeTools:
    """The manifest references point at stale tokens: only a typed secret gets through."""
    tools.reset()
    for k, v in {
        "JIRA_URL": tools.url_for("jira"),
        "JIRA_READ_EMAIL": "z-read@example.test",
        "JIRA_READ_TOKEN": "stale-" + "read-0000",
        "JIRA_WRITE_EMAIL": "z-write@example.test",
        "JIRA_WRITE_TOKEN": "stale-" + "write-0000",
    }.items():
        monkeypatch.setenv(k, v)
    return tools


@pytest.fixture
def app(make_client: Callable[..., TestClient], tmp_path: Any, env: FakeTools) -> TestClient:
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "artifacts")
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


def as_user(app: TestClient, jars: dict[str, dict[str, str]], user: str) -> dict[str, str]:
    use_jar(app, jars[user])
    return csrf(app)


def run_db(app: TestClient, fn: Callable[[Any], Any]) -> Any:
    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> Any:
        async with state.sessions() as db, db.begin():
            return await fn(db)

    return app.portal.call(go)  # type: ignore[union-attr]


def mcp_call(app: TestClient, runner: ScriptedRunner, name: str, args: dict[str, Any]) -> dict[str, Any]:
    resp = app.post(
        "/gateway/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
        headers={"Authorization": f"Bearer {runner.session}", "Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["result"])


def start(app: TestClient, jars: dict[str, dict[str, str]], writes: list[str]) -> ScriptedRunner:
    h = as_user(app, jars, "bob")
    plan = {"steps": [{"id": "act", "agent": "ticket-manager", "prompt": "Triage", "writes": writes}], "writes": writes}
    created = app.post(
        "/api/v1/missions", json={"title": "T", "prompt": "T", "plan": plan, "connectors": ["jira"]}, headers=h
    )
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    return runner


def type_secret(app: TestClient, jars: dict[str, dict[str, str]], purpose: str, value: str, **extra: Any) -> Any:
    h = as_user(app, jars, "alice")
    return app.put(f"/api/v1/connectors/jira/credentials/{purpose}", json={"value": value, **extra}, headers=h)


def credential(connector: dict[str, Any], purpose: str) -> dict[str, Any]:
    return next(c for c in connector["credentials"] if c["purpose"] == purpose and c["scope"] == "service")


def executor_tick(app: TestClient) -> dict[str, list[str]]:
    from z_server.gateway import executor

    state = app.app.state.z  # type: ignore[attr-defined]
    return app.portal.call(executor.tick, state, state.gateway)  # type: ignore[union-attr,no-any-return]


def test_typed_secrets_are_used_by_purpose_and_never_shown(
    app: TestClient, jars: dict[str, dict[str, str]], env: FakeTools, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    read, write = env.token("jira", "read"), env.token("jira", "write")

    # with the stale manifest references, reads are refused by the tool
    runner = start(app, jars, [])
    assert mcp_call(app, runner, "jira__issues__search", SEARCH)["isError"] is True

    typed = type_secret(app, jars, "read", read + "\n")  # a pasted trailing newline is dropped
    assert typed.status_code == 200, typed.text
    cred = credential(typed.json(), "read")
    assert cred["backend"] == "sealed" and cred["ref"] == "sealed:jira/read" and cred["rotated_at"]
    assert cred["identity"] == "z-read@example.test"
    assert type_secret(app, jars, "write", write, username="z-write@example.test").status_code == 200

    # agent calls use the typed read secret
    runner = start(app, jars, ["jira.issues.create"])
    found = mcp_call(app, runner, "jira__issues__search", SEARCH)
    assert found["isError"] is False, found
    assert '"issues"' in found["content"][0]["text"]

    # the executor uses the typed write secret
    proposed = runner.call(
        "POST",
        "/actions",
        json={"connector": "jira", "operation": "jira.issues.create", "summary": "s", "payload": ISSUE},
    )
    assert proposed.status_code == 201, proposed.text
    action_id = proposed.json()["id"]
    h = as_user(app, jars, "carol")
    assert app.post(f"/api/v1/actions/{action_id}/approve", json={}, headers=h).json()["status"] == "approved"
    assert executor_tick(app)["applied"] == [action_id]
    [created] = env.writes("jira.issues.create")
    assert created["token_kind"] == "write"

    # the values never appear: API, audit rows, events, logs
    as_user(app, jars, "alice")
    shown = app.get("/api/v1/connectors/jira").text + app.get("/api/v1/connectors").text
    from z_server.db.models import AuditEvent, Credential, Event

    async def dump(db: Any) -> tuple[str, str, list[Any]]:
        audit = [
            {k: getattr(r, k) for k in ("action", "target_id", "before", "after")}
            for r in (await db.execute(select(AuditEvent))).scalars()
        ]
        events = [r.payload for r in (await db.execute(select(Event))).scalars()]
        sealed = [r.sealed_value for r in (await db.execute(select(Credential))).scalars() if r.sealed_value]
        return json.dumps(audit, default=str), json.dumps(events, default=str), sealed

    audit_text, events_text, sealed = run_db(app, dump)
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for value in (read, write):
        assert value not in shown and value not in audit_text and value not in events_text and value not in logs
        assert all(value.encode() not in blob for blob in sealed)  # stored sealed, not in clear
    entries = app.get("/api/v1/audit", params={"action": "connector.secret_set"}).json()["items"]
    assert {e["after"]["purpose"] for e in entries} == {"read", "write"}
    assert all(e["after"]["fingerprint"].startswith("hmac-sha256:") for e in entries)


def test_reseed_and_manifest_sync_keep_typed_secrets_and_clear_restores_the_reference(
    app: TestClient, jars: dict[str, dict[str, str]], env: FakeTools
) -> None:
    assert type_secret(app, jars, "read", env.token("jira", "read")).status_code == 200

    # a pack re-seed and a manifest edit (same references) keep it
    seed_packs(app)
    as_user(app, jars, "alice")
    connector = app.get("/api/v1/connectors/jira").json()
    patched = app.patch("/api/v1/connectors/jira", json={"manifest": connector["manifest"]}, headers=csrf(app))
    assert patched.status_code == 200, patched.text
    assert credential(patched.json(), "read")["backend"] == "sealed"
    assert credential(patched.json(), "write")["ref"] == "env:JIRA_WRITE_TOKEN"
    runner = start(app, jars, [])
    assert mcp_call(app, runner, "jira__issues__search", SEARCH)["isError"] is False

    h = as_user(app, jars, "alice")
    cleared = app.delete("/api/v1/connectors/jira/credentials/read", headers=h)
    assert cleared.status_code == 200, cleared.text
    cred = credential(cleared.json(), "read")
    assert cred["backend"] == "env" and cred["ref"] == "env:JIRA_READ_TOKEN"
    assert cred["identity"] == "z-read@example.test"
    assert app.delete("/api/v1/connectors/jira/credentials/read", headers=h).status_code == 404

    async def sealed_values(db: Any) -> list[Any]:
        from z_server.db.models import Credential

        return [r.sealed_value for r in (await db.execute(select(Credential))).scalars() if r.sealed_value]

    assert run_db(app, sealed_values) == []
    runner = start(app, jars, [])  # back to the (stale) manifest reference
    assert mcp_call(app, runner, "jira__issues__search", SEARCH)["isError"] is True
    as_user(app, jars, "alice")
    assert app.get("/api/v1/audit", params={"action": "connector.secret_cleared"}).json()["items"]


def test_typed_secret_refusals(app: TestClient, jars: dict[str, dict[str, str]], env: FakeTools) -> None:
    read = env.token("jira", "read")
    assert type_secret(app, jars, "admin", read).status_code == 422
    assert type_secret(app, jars, "read", "x" * 16385).status_code == 422
    assert type_secret(app, jars, "read", "").status_code == 422
    assert type_secret(app, jars, "read", "\n").status_code == 422
    h = as_user(app, jars, "alice")
    assert app.put("/api/v1/connectors/nope/credentials/read", json={"value": "v"}, headers=h).status_code == 404
    no_write = app.put("/api/v1/connectors/cmdb/credentials/write", json={"value": "v"}, headers=h)
    assert no_write.status_code == 422 and "does not declare a write secret" in no_write.json()["detail"]
    # read and write stay different secrets (invariant 7)
    assert type_secret(app, jars, "read", read).status_code == 200
    same = type_secret(app, jars, "write", read)
    assert same.status_code == 422 and "invariant 7" in same.json()["detail"]
    h = as_user(app, jars, "bob")
    assert app.put("/api/v1/connectors/jira/credentials/read", json={"value": "v"}, headers=h).status_code == 403
    assert app.delete("/api/v1/connectors/jira/credentials/read", headers=h).status_code == 403


def test_injection_looks_up_the_typed_secret_of_its_own_purpose_only() -> None:
    """Invariant 7: a typed write secret never reaches a read call, and the reverse; equal values are refused."""
    import asyncio

    import httpx
    from z_server.gateway.auth import AuthError, material
    from z_server.gateway.secrets import SecretResolver, TypedSecret

    manifest = {
        "name": "jira",
        "auth": {
            "read": {"type": "bearer", "secret": "env:R"},
            "write": {"type": "basic", "username": "w@example.test", "secret": "env:W"},
        },
    }
    asked: list[tuple[str, str]] = []

    def resolver(client: httpx.AsyncClient, typed: dict[str, TypedSecret]) -> SecretResolver:
        async def lookup(connector: str, purpose: str) -> TypedSecret | None:
            asked.append((connector, purpose))
            return typed.get(purpose)

        return SecretResolver(client, env={"R": "env-read", "W": "env-write"}, sealed=lookup)

    write_only = {"write": TypedSecret("sealed:jira/write", "typed-write", "other@example.test")}
    same = {"read": TypedSecret("sealed:jira/read", "same"), "write": TypedSecret("sealed:jira/write", "same")}

    async def go() -> tuple[Any, Any]:
        async with httpx.AsyncClient() as client:
            r = resolver(client, write_only)
            read, write = await material(manifest, "read", r), await material(manifest, "write", r)
            with pytest.raises(AuthError, match="invariant 7"):
                await material(manifest, "read", resolver(client, same))
            return read, write

    read, write = asyncio.run(go())
    assert read.headers == {"Authorization": "Bearer env-read"} and read.secrets == ["env-read"]
    assert write.identity == "other@example.test" and "typed-write" in write.secrets
    assert ("jira", "read") in asked and ("jira", "write") in asked
    assert "typed-write" not in repr(write_only["write"])  # a repr never shows the value
