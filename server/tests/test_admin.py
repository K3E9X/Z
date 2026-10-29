"""Admin area endpoints not covered elsewhere (docs/12 §3) and the break-glass account."""

from __future__ import annotations

import re
from collections.abc import Callable

import pyotp
import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import base_config, csrf, sign_in_as, use_jar


@pytest.fixture
def admin(client: TestClient, oidc: FakeGitLabOIDC) -> TestClient:
    sign_in_as(client, oidc, "bob")
    sign_in_as(client, oidc, "alice")
    return client


def _user_id(client: TestClient, username: str) -> str:
    return next(
        u["id"]
        for u in client.get("/api/v1/admin/users", params={"q": username}).json()["items"]
        if u["username"] == username
    )


def test_user_list_filters_and_pagination(admin: TestClient) -> None:
    first = admin.get("/api/v1/admin/users", params={"limit": 1}).json()
    assert len(first["items"]) == 1 and first["next_cursor"]
    second = admin.get("/api/v1/admin/users", params={"limit": 1, "cursor": first["next_cursor"]}).json()
    assert second["items"][0]["id"] != first["items"][0]["id"]
    admins = admin.get("/api/v1/admin/users", params={"role": "admin"}).json()["items"]
    assert [u["username"] for u in admins] == ["alice"]


def test_user_detail_and_revoke_sessions(admin: TestClient, oidc: FakeGitLabOIDC) -> None:
    bob_id = _user_id(admin, "bob")
    detail = admin.get(f"/api/v1/admin/users/{bob_id}").json()
    assert detail["username"] == "bob" and len(detail["sessions"]) == 1 and detail["recent_activity"]
    assert (
        admin.post(
            f"/api/v1/admin/users/{bob_id}/revoke-sessions", json={"reason": "laptop lost"}, headers=csrf(admin)
        ).status_code
        == 204
    )
    assert admin.get(f"/api/v1/admin/users/{bob_id}").json()["sessions"] == []
    assert admin.get("/api/v1/admin/users/nope").status_code == 404


def test_admin_cannot_suspend_self(admin: TestClient) -> None:
    resp = admin.patch(
        f"/api/v1/admin/users/{_user_id(admin, 'alice')}", json={"action": "suspend"}, headers=csrf(admin)
    )
    assert resp.status_code == 403 and resp.json()["type"] == "urn:z:problem:separation-of-duties"


def test_bindings_list_filter_and_delete(admin: TestClient) -> None:
    bob = _user_id(admin, "bob")
    created = admin.post(
        "/api/v1/admin/role-bindings",
        json={"subject": f"user:{bob}", "role": "builder", "scope": "org"},
        headers=csrf(admin),
    ).json()
    assert [b["id"] for b in admin.get("/api/v1/admin/role-bindings", params={"subject": f"user:{bob}"}).json()] == [
        created["id"]
    ]
    assert admin.get("/api/v1/admin/role-bindings", params={"role": "auditor"}).json() == []
    assert admin.delete(f"/api/v1/admin/role-bindings/{created['id']}", headers=csrf(admin)).status_code == 204
    assert admin.delete(f"/api/v1/admin/role-bindings/{created['id']}", headers=csrf(admin)).status_code == 404


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"subject": "user:usr_missing", "role": "auditor", "scope": "org"}, 422),
        ({"subject": "group:cyber/x", "role": "made-up", "scope": "org"}, 422),
        ({"subject": "group:cyber/x", "role": "auditor", "scope": "team:team_missing"}, 422),
        ({"subject": "group:cyber/x", "role": "auditor", "scope": "org", "expires_at": "2001-01-01T00:00:00Z"}, 422),
    ],
)
def test_binding_validation(admin: TestClient, body: dict[str, str], status: int) -> None:
    assert admin.post("/api/v1/admin/role-bindings", json=body, headers=csrf(admin)).status_code == status


def test_custom_roles(admin: TestClient, oidc: FakeGitLabOIDC) -> None:
    roles = {r["name"]: r for r in admin.get("/api/v1/admin/roles").json()}
    assert roles["viewer"]["builtin"] and "missions:read" in roles["viewer"]["permissions"]
    assert set(roles["admin"]["permissions"]) == {
        p["permission"] for p in admin.get("/api/v1/admin/permissions").json()
    }
    assert (
        admin.post("/api/v1/admin/roles", json={"name": "viewer", "permissions": []}, headers=csrf(admin)).status_code
        == 409
    )
    assert (
        admin.post(
            "/api/v1/admin/roles", json={"name": "reporter", "permissions": ["nope:nope"]}, headers=csrf(admin)
        ).status_code
        == 422
    )
    created = admin.post(
        "/api/v1/admin/roles",
        json={"name": "reporter", "permissions": ["analytics:read", "usage:read"]},
        headers=csrf(admin),
    )
    assert created.status_code == 201
    role_id = created.json()["id"]
    assert (
        admin.post("/api/v1/admin/roles", json={"name": "reporter", "permissions": []}, headers=csrf(admin)).status_code
        == 409
    )
    patched = admin.patch(
        f"/api/v1/admin/roles/{role_id}",
        json={"name": "reporter", "permissions": ["audit:read_own"]},
        headers=csrf(admin),
    )
    assert patched.json()["permissions"] == ["audit:read_own"]
    assert (
        admin.patch(
            "/api/v1/admin/roles/builtin:viewer", json={"name": "viewer", "permissions": []}, headers=csrf(admin)
        ).status_code
        == 404
    )
    bob = _user_id(admin, "bob")
    admin.post(
        "/api/v1/admin/role-bindings",
        json={"subject": f"user:{bob}", "role": "reporter", "scope": "org"},
        headers=csrf(admin),
    )
    who = admin.get("/api/v1/admin/who-can", params={"permission": "audit:read_own"}).json()
    assert any(e["user"] == "bob" and e["via"].startswith("reporter") for e in who["users"])
    assert admin.get("/api/v1/admin/who-can", params={"permission": "x:y"}).status_code == 422


def test_admin_sessions_and_tokens(admin: TestClient, oidc: FakeGitLabOIDC) -> None:
    bob_jar = sign_in_as(admin, oidc, "bob")
    use_jar(admin, bob_jar)
    token = admin.post(
        "/api/v1/me/tokens", json={"name": "ci", "scopes": ["missions:read"], "expires_in_days": 5}, headers=csrf(admin)
    ).json()
    assert (
        admin.post(
            "/api/v1/me/tokens",
            json={"name": "ci", "scopes": ["missions:read"], "expires_in_days": 200},
            headers=csrf(admin),
        ).status_code
        == 422
    )
    sessions = admin.get("/api/v1/me/sessions").json()
    assert any(s["current"] for s in sessions)
    sign_in_as(admin, oidc, "alice")
    all_sessions = admin.get("/api/v1/admin/sessions", params={"user": "bob"}).json()["items"]
    assert all_sessions and all(s["user"] == "bob" for s in all_sessions)
    tokens = admin.get("/api/v1/admin/tokens", params={"user": "bob"}).json()["items"]
    assert [t["id"] for t in tokens] == [token["id"]]
    assert admin.delete(f"/api/v1/admin/tokens/{token['id']}", headers=csrf(admin)).status_code == 204
    for s in all_sessions:
        assert admin.delete(f"/api/v1/admin/sessions/{s['id']}", headers=csrf(admin)).status_code == 204
    use_jar(admin, bob_jar)
    assert admin.get("/api/v1/me").status_code == 401
    assert admin.delete("/api/v1/admin/sessions/nope", headers=csrf(sign_in_and(admin, oidc))).status_code == 404


def sign_in_and(client: TestClient, oidc: FakeGitLabOIDC) -> TestClient:
    sign_in_as(client, oidc, "alice")
    return client


def test_my_session_revocation(admin: TestClient, oidc: FakeGitLabOIDC) -> None:
    mine = admin.get("/api/v1/me/sessions").json()
    assert admin.delete("/api/v1/me/sessions/ses_unknown", headers=csrf(admin)).status_code == 404
    assert admin.delete(f"/api/v1/me/sessions/{mine[0]['id']}", headers=csrf(admin)).status_code == 204
    assert admin.get("/api/v1/me").status_code == 401


def test_teams_crud(admin: TestClient) -> None:
    teams = admin.get("/api/v1/teams").json()
    assert {t["name"] for t in teams} >= {"Cyber Ops", "Architecture"}
    created = admin.post(
        "/api/v1/teams", json={"name": "Platform", "monthly_token_budget": 1000000}, headers=csrf(admin)
    ).json()
    assert admin.post("/api/v1/teams", json={"name": "Platform"}, headers=csrf(admin)).status_code == 409
    assert admin.get(f"/api/v1/teams/{created['id']}").json()["monthly_token_budget"] == 1000000
    patched = admin.patch(f"/api/v1/teams/{created['id']}", json={"name": "Platform Eng"}, headers=csrf(admin)).json()
    assert patched["name"] == "Platform Eng"
    assert admin.get("/api/v1/teams/team_missing").status_code == 404
    assert admin.patch("/api/v1/teams/team_missing", json={"name": "x"}, headers=csrf(admin)).status_code == 404


def test_auth_settings_and_audit_settings(admin: TestClient, oidc: FakeGitLabOIDC) -> None:
    settings = admin.get("/api/v1/admin/auth-settings").json()
    assert settings["jwks"]["ok"] is True and settings["client_id"] == "z-web"
    settings["session"]["idle_minutes"] = 30
    saved = admin.put("/api/v1/admin/auth-settings", json=settings, headers=csrf(admin)).json()
    assert saved["session"]["idle_minutes"] == 30 and saved.get("client_secret") is None
    audit = admin.get("/api/v1/admin/audit-settings").json()
    assert audit["retention_days"] == 365
    audit["retention_days"] = 400
    assert admin.put("/api/v1/admin/audit-settings", json=audit, headers=csrf(admin)).json()["retention_days"] == 400
    audit["retention_days"] = 30
    assert admin.put("/api/v1/admin/audit-settings", json=audit, headers=csrf(admin)).status_code == 422
    changes = admin.get("/api/v1/audit", params={"action": "admin."}).json()["items"]
    assert {"admin.auth_settings_changed", "admin.audit_settings_changed"} <= {c["action"] for c in changes}
    assert all(c["before"] is not None for c in changes if c["action"] == "admin.audit_settings_changed")


def test_auth_settings_test_reports_unreachable_issuer(admin: TestClient) -> None:
    settings = admin.get("/api/v1/admin/auth-settings").json()
    settings["issuer_url"] = "http://127.0.0.1:1"
    assert admin.put("/api/v1/admin/auth-settings", json=settings, headers=csrf(admin)).status_code == 200
    result = admin.post("/api/v1/admin/auth-settings:test", json={}, headers=csrf(admin)).json()
    assert result["ok"] is False and result["discovery"] is False


def test_breakglass_needs_totp(
    make_client: Callable[..., TestClient],
    oidc: FakeGitLabOIDC,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = base_config(oidc.issuer, **{"auth.breakglass.enabled": True})
    with caplog.at_level("DEBUG"):
        client = make_client(config, breakglass=True)
    # shown once on stdout (Compose) or written to Z_BREAKGLASS_SECRET_FILE (Kubernetes), never logged
    message = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("break-glass:"))
    password = re.search(r"password (\S+)", message).group(1)  # type: ignore[union-attr]
    secret = re.search(r"secret=([A-Z2-7]+)", message).group(1)  # type: ignore[union-attr]
    assert not [r for r in caplog.records if password in r.getMessage() or secret in r.getMessage()]
    assert (
        client.post("/api/v1/auth/local/login", json={"username": "z-breakglass", "password": password}).status_code
        == 401
    )
    ok = client.post(
        "/api/v1/auth/local/login",
        json={"username": "z-breakglass", "password": password, "totp": pyotp.TOTP(secret).now()},
    )
    assert ok.status_code == 200 and ok.json()["banners"][0]["kind"] == "breakglass"
    assert client.get("/api/v1/admin/users").status_code == 200
    step = client.post(
        "/api/v1/auth/step-up", json={"password": password, "totp": pyotp.TOTP(secret).now()}, headers=csrf(client)
    )
    assert step.status_code == 200 and step.json()["step_up_at"]
    bad = client.post("/api/v1/auth/step-up", json={"password": "wrong"}, headers=csrf(client))
    assert bad.status_code == 401


def test_dev_step_up_and_cli_exchange(make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC) -> None:
    client = make_client(base_config(oidc.issuer, **{"auth.mode": "dev"}))
    assert client.post("/api/v1/auth/step-up", json={}).status_code == 200
    token = client.post("/api/v1/auth/cli/exchange", json={"dev": True, "device_name": "dev"}).json()["token"]
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).json()["session"]["kind"] == "cli"
    assert client.get("/api/v1/auth/login", follow_redirects=False).headers["location"] == "/"


def test_dev_exchange_refused_outside_dev_mode(client: TestClient) -> None:
    assert client.post("/api/v1/auth/cli/exchange", json={"dev": True, "device_name": "x"}).status_code == 403


def test_login_json_redirect(client: TestClient) -> None:
    resp = client.get("/api/v1/auth/login", headers={"accept": "application/json"})
    assert resp.status_code == 200 and resp.json()["redirect_url"].startswith("http://127.0.0.1")


def test_callback_without_code_and_provider_error(client: TestClient) -> None:
    assert (
        client.get("/api/v1/auth/callback", params={"error": "access_denied"}, follow_redirects=False).headers[
            "location"
        ]
        == "/signin?error=provider_error"
    )
    assert (
        client.get("/api/v1/auth/callback", params={"state": "nope", "code": "x"}, follow_redirects=False).headers[
            "location"
        ]
        == "/signin?error=invalid_state"
    )


def test_blocked_user_cannot_sign_in(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    oidc.update_user("bob", blocked=True)
    client.cookies.clear()
    from tests.support.server import login

    assert login_location(client, oidc, login) == "/signin?error=provider_error"


def login_location(client: TestClient, oidc: FakeGitLabOIDC, login: Callable[..., object]) -> str:
    resp = login(client, oidc, "bob")
    return str(resp.headers["location"])  # type: ignore[attr-defined]


def test_readyz_and_metrics(client: TestClient) -> None:
    assert client.get("/readyz").json()["checks"]["database"]["ok"] is True
    assert client.get("/metrics").status_code == 200


def test_metrics_need_the_token_when_set(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Z_METRICS_TOKEN", "m3trics-token-for-tests")
    assert client.get("/metrics").status_code == 404
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 404
    assert client.get("/metrics", headers={"Authorization": "Bearer m3trics-token-for-tests"}).status_code == 200
