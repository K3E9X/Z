"""RBAC matrix, local accounts, separation of duties, CSRF, API tokens, audit chain, HTTP hardening (docs/12 §6)."""

from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest
from fastapi.testclient import TestClient
from z_server.settings import Settings, SettingsError

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import base_config, csrf, sign_in_as, use_jar

ROLE_USERS = {
    "admin": "alice",
    "operator": "bob",
    "approver": "carol",
    "auditor": "dave",
    "builder": "erin",
    "viewer": "victor",
}
# (method, path, roles that are allowed) for the routes built so far; everything else must be 403
MATRIX = [
    ("GET", "/api/v1/admin/users", {"admin"}),
    ("GET", "/api/v1/admin/permissions", {"admin"}),
    ("GET", "/api/v1/teams", set(ROLE_USERS)),
    ("GET", "/api/v1/settings/guardrails", set(ROLE_USERS)),
    ("GET", "/api/v1/audit", set(ROLE_USERS)),
    ("GET", "/api/v1/audit/export", {"admin", "auditor"}),
    ("GET", "/api/v1/admin/group-mapping", {"admin"}),
]


@pytest.fixture
def jars(client: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    return {role: sign_in_as(client, oidc, user) for role, user in ROLE_USERS.items()}


@pytest.mark.parametrize(("method", "path", "allowed"), MATRIX)
def test_permission_matrix(
    client: TestClient, jars: dict[str, dict[str, str]], method: str, path: str, allowed: set[str]
) -> None:
    for role, jar in jars.items():
        use_jar(client, jar)
        resp = client.request(method, path)
        if role in allowed:
            assert resp.status_code == 200, (role, path, resp.text)
        else:
            assert resp.status_code == 403, (role, path, resp.status_code)
            assert resp.json()["type"] == "urn:z:problem:forbidden"


def test_unauthenticated_is_401_problem(client: TestClient) -> None:
    resp = client.get("/api/v1/admin/users")
    assert resp.status_code == 401
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["type"] == "urn:z:problem:unauthenticated"


def test_denials_are_audited(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "bob")
    assert client.get("/api/v1/admin/users").status_code == 403
    admin = sign_in_as(client, oidc, "alice")
    use_jar(client, admin)
    events = client.get("/api/v1/audit", params={"action": "authz.denied"}).json()["items"]
    assert events and events[0]["actor"] == "bob" and events[0]["result"] == "denied"


def test_users_see_only_their_own_audit_events(client: TestClient, jars: dict[str, dict[str, str]]) -> None:
    use_jar(client, jars["operator"])
    assert {e["actor"] for e in client.get("/api/v1/audit").json()["items"]} == {"bob"}
    use_jar(client, jars["auditor"])
    assert len({e["actor"] for e in client.get("/api/v1/audit").json()["items"]}) > 1


def test_csrf_is_required_for_cookie_sessions(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "alice")
    assert client.post("/api/v1/teams", json={"name": "Red"}).status_code == 403
    assert client.post("/api/v1/teams", json={"name": "Red"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/v1/teams", json={"name": "Red"}, headers=csrf(client)).status_code == 201


def test_admin_cannot_change_own_roles(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "alice")
    me = client.get("/api/v1/me").json()["user"]
    resp = client.post(
        "/api/v1/admin/role-bindings",
        json={"subject": f"user:{me['id']}", "role": "auditor", "scope": "org"},
        headers=csrf(client),
    )
    assert resp.status_code == 403 and resp.json()["type"] == "urn:z:problem:separation-of-duties"


def test_manual_role_grant_with_expiry(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    bob_jar = sign_in_as(client, oidc, "bob")
    sign_in_as(client, oidc, "alice")
    bob = next(u for u in client.get("/api/v1/admin/users").json()["items"] if u["username"] == "bob")
    from datetime import UTC, datetime, timedelta

    expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    grant = client.post(
        "/api/v1/admin/role-bindings",
        json={
            "subject": f"user:{bob['id']}",
            "role": "auditor",
            "scope": "org",
            "expires_at": expires,
            "reason": "audit week",
        },
        headers=csrf(client),
    )
    assert grant.status_code == 201, grant.text
    use_jar(client, bob_jar)
    roles = {(r["role"], r["source"]) for r in client.get("/api/v1/me").json()["user"]["effective_roles"]}
    assert ("auditor", "manual") in roles and ("operator", "gitlab") in roles
    assert client.get("/api/v1/audit/export").status_code == 200


def test_group_mapping_dry_run_and_change(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "bob")
    sign_in_as(client, oidc, "alice")
    mapping = client.get("/api/v1/admin/group-mapping").json()
    for m in mapping["group_mapping"]:
        if m["group"] == "cyber/z/z-operators":
            m["role"] = "approver"
    dry = client.post("/api/v1/admin/group-mapping:dry-run", json=mapping, headers=csrf(client)).json()
    assert {"user": "bob", "change": "gain", "role": "approver"}.items() <= next(
        c for c in dry["changes"] if c["change"] == "gain"
    ).items()
    assert any(c["user"] == "bob" and c["change"] == "loss" and c["role"] == "operator" for c in dry["changes"])
    assert client.put("/api/v1/admin/group-mapping", json=mapping, headers=csrf(client)).status_code == 200
    bob = sign_in_as(client, oidc, "bob")
    use_jar(client, bob)
    assert {r["role"] for r in client.get("/api/v1/me").json()["user"]["effective_roles"]} == {"approver"}


def test_mapping_must_keep_an_admin_group(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "alice")
    mapping = client.get("/api/v1/admin/group-mapping").json()
    mapping["group_mapping"] = [m for m in mapping["group_mapping"] if m["role"] != "admin"]
    resp = client.put("/api/v1/admin/group-mapping", json=mapping, headers=csrf(client))
    assert resp.status_code == 422


def test_api_tokens_are_scoped_shown_once_and_revocable(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "dave")
    created = client.post(
        "/api/v1/me/tokens",
        json={"name": "export", "scopes": ["audit:read", "audit:export"], "expires_in_days": 7},
        headers=csrf(client),
    )
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith("zt_")
    assert all(t["token"] is None for t in client.get("/api/v1/me/tokens").json())
    too_much = client.post(
        "/api/v1/me/tokens", json={"name": "x", "scopes": ["users:manage"], "expires_in_days": 7}, headers=csrf(client)
    )
    assert too_much.status_code == 403
    client.cookies.clear()
    bearer = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/audit", headers=bearer).status_code == 200
    assert client.get("/api/v1/teams", headers=bearer).status_code == 403  # teams:read not in the token scopes
    use_jar(client, sign_in_as(client, oidc, "dave"))
    token_id = created.json()["id"]
    assert client.delete(f"/api/v1/me/tokens/{token_id}", headers=csrf(client)).status_code == 204
    client.cookies.clear()
    assert client.get("/api/v1/audit", headers=bearer).status_code == 401


def test_guardrails_locked_values_cannot_change(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "alice")
    current = client.get("/api/v1/settings/guardrails").json()
    assert current["human_approval_for_writes"] == {
        "value": True,
        "locked": True,
        "description": current["human_approval_for_writes"]["description"],
    }
    locked = client.put(
        "/api/v1/settings/guardrails",
        json={"human_approval_for_writes": {"value": False, "locked": True}},
        headers=csrf(client),
    )
    assert locked.status_code == 409 and locked.json()["type"] == "urn:z:problem:locked"
    ok = client.put(
        "/api/v1/settings/guardrails", json={"max_steps_per_job": {"value": 80, "locked": False}}, headers=csrf(client)
    )
    assert ok.status_code == 200 and ok.json()["max_steps_per_job"]["value"] == 80


def test_ssrf_guard_on_admin_urls(client: TestClient, oidc: FakeGitLabOIDC, monkeypatch: pytest.MonkeyPatch) -> None:
    sign_in_as(client, oidc, "alice")
    monkeypatch.setenv("Z_OUTBOUND_ALLOW", "")
    settings = client.get("/api/v1/admin/auth-settings").json()
    for url in ("http://169.254.169.254/latest", "http://127.0.0.1:9999", "http://10.43.0.10"):
        settings["issuer_url"] = url
        resp = client.put("/api/v1/admin/auth-settings", json=settings, headers=csrf(client))
        assert resp.status_code == 422, url


# ---------------------------------------------------------------- local accounts


def bootstrap_password(capsys: pytest.CaptureFixture[str]) -> str:
    out = capsys.readouterr().out
    line = next(line for line in out.splitlines() if line.startswith("bootstrap:"))
    return line.split("one-time password ")[1].split(" ")[0]


def test_bootstrap_configures_sign_in_then_is_disabled(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, capsys: pytest.CaptureFixture[str]
) -> None:
    config = base_config(oidc.issuer)
    config["auth"]["issuer_url"] = ""
    config["auth"]["client_id"] = ""
    client = make_client(config, oidc_client_secret="")
    password = bootstrap_password(capsys)
    assert client.get("/api/v1/auth/login", follow_redirects=False).status_code == 503  # GitLab not configured yet
    resp = client.post("/api/v1/auth/local/login", json={"username": "z-bootstrap", "password": password})
    assert resp.status_code == 200, resp.text
    assert resp.json()["banners"][0]["kind"] == "local_account"
    assert client.get("/api/v1/admin/users").status_code == 403  # bootstrap: sign-in and mapping only
    settings = client.get("/api/v1/admin/auth-settings").json()
    assert settings["local_accounts"]["bootstrap"]["state"] == "active"
    settings.update(issuer_url=oidc.issuer, client_id="z-web", client_secret="z-web-secret")
    assert client.put("/api/v1/admin/auth-settings", json=settings, headers=csrf(client)).status_code == 200
    assert client.post("/api/v1/admin/auth-settings:test", json={}, headers=csrf(client)).json()["ok"] is True
    assert (
        "client_secret" not in client.get("/api/v1/admin/auth-settings").json()
        or client.get("/api/v1/admin/auth-settings").json().get("client_secret") is None
    )
    sign_in_as(client, oidc, "alice")  # first GitLab admin
    client.cookies.clear()
    again = client.post("/api/v1/auth/local/login", json={"username": "z-bootstrap", "password": password})
    assert again.status_code == 401


def test_local_login_locks_after_five_failures(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, capsys: pytest.CaptureFixture[str]
) -> None:
    client = make_client()
    password = bootstrap_password(capsys)
    for _ in range(5):
        assert (
            client.post("/api/v1/auth/local/login", json={"username": "z-bootstrap", "password": "wrong"}).status_code
            == 401
        )
    assert (
        client.post("/api/v1/auth/local/login", json={"username": "z-bootstrap", "password": password}).status_code
        == 401
    )  # locked


def test_test_accounts_cannot_be_admin_and_are_refused_in_production(database_url: str, oidc: FakeGitLabOIDC) -> None:
    config = base_config(oidc.issuer)
    with pytest.raises(SettingsError):
        Settings(database_url=database_url, secret_key="x" * 40, env="production", config=config).check()
    config["auth"]["local_accounts"]["test_accounts"] = [{"name": "tester", "role": "admin"}]
    from z_common.contracts import ContractError, validate

    with pytest.raises(ContractError):
        validate("config.schema.json", config)


def test_test_account_signs_in_with_its_role(make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC) -> None:
    client = make_client(
        test_account_passwords="tester=correct-horse-battery-1;tester-approver=correct-horse-battery-2"
    )
    resp = client.post("/api/v1/auth/local/login", json={"username": "tester", "password": "correct-horse-battery-1"})
    assert resp.status_code == 200
    roles = resp.json()["user"]["effective_roles"]
    assert [r["role"] for r in roles] == ["operator"] and roles[0]["scope"].startswith("team:")
    assert client.get("/api/v1/admin/users").status_code == 403


def test_dev_mode_is_refused_in_production(database_url: str, oidc: FakeGitLabOIDC) -> None:
    config = base_config(oidc.issuer, **{"auth.mode": "dev"})
    config["auth"]["local_accounts"]["test_accounts"] = []
    with pytest.raises(SettingsError, match="dev"):
        Settings(database_url=database_url, secret_key="x" * 40, env="production", config=config).check()


def test_dev_mode_signs_in_the_dev_user(make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC) -> None:
    client = make_client(base_config(oidc.issuer, **{"auth.mode": "dev"}))
    me = client.get("/api/v1/me").json()
    assert me["user"]["username"] == "dev" and me["user"]["local_kind"] == "dev"


# ---------------------------------------------------------------- audit


def test_audit_chain_verifies_and_detects_tampering(
    client: TestClient, oidc: FakeGitLabOIDC, database_url: str
) -> None:
    sign_in_as(client, oidc, "alice")
    client.post("/api/v1/teams", json={"name": "Blue"}, headers=csrf(client))
    ok = client.post("/api/v1/admin/audit:verify", json={}, headers=csrf(client)).json()
    assert ok["ok"] is True and ok["checked"] >= 2
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as conn:  # the owner role, bypassing the append-only trigger
        conn.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only")
        conn.execute("UPDATE audit_events SET actor = 'someone-else' WHERE seq = 1")
        conn.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only")
    bad = client.post("/api/v1/admin/audit:verify", json={}, headers=csrf(client)).json()
    assert bad["ok"] is False and bad["first_bad_seq"] == 1


def test_audit_checkpoints_are_signed(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    from z_server import audit

    sign_in_as(client, oidc, "alice")
    state = client.app.state.z  # type: ignore[attr-defined]

    async def make() -> None:
        async with state.sessions() as db, db.begin():
            await audit.checkpoint(db, state.signer)

    client.portal.call(make)  # type: ignore[union-attr]
    result = client.post("/api/v1/admin/audit:verify", json={}, headers=csrf(client)).json()
    assert result["ok"] is True and result["checkpoints_verified"] == 1


def test_audit_export_formats(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "alice")
    jsonl = client.get("/api/v1/audit/export", params={"format": "json"})
    assert jsonl.status_code == 200 and jsonl.headers["content-disposition"].startswith("attachment")
    first = __import__("json").loads(jsonl.text.splitlines()[0])
    assert {"seq", "hash", "prev_hash", "actor"} <= set(first)
    assert client.get("/api/v1/audit/export", params={"format": "syslog"}).text.startswith("<")
    assert client.get("/api/v1/audit/export", params={"format": "csv"}).text.startswith("seq,ts")
    exports = client.get("/api/v1/audit", params={"action": "audit.exported"}).json()["items"]
    assert len(exports) == 3


def test_app_role_cannot_rewrite_audit(database_url: str, client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "alice")
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    app_dsn = dsn.replace("//z:z@", "//z_app:z_app@")
    with psycopg.connect(app_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE audit_events SET actor = 'x'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM audit_events")


# ---------------------------------------------------------------- HTTP hardening


def test_security_headers(client: TestClient) -> None:
    resp = client.get("/healthz")
    for header in (
        "content-security-policy",
        "strict-transport-security",
        "x-content-type-options",
        "referrer-policy",
        "x-request-id",
    ):
        assert header in resp.headers
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]


def test_validation_errors_are_problem_details(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "alice")
    resp = client.post("/api/v1/teams", json={"nom": "x"}, headers=csrf(client))
    assert resp.status_code == 422
    body = resp.json()
    assert body["type"] == "urn:z:problem:validation" and body["errors"]
