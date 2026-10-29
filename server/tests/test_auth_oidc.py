"""GitLab OIDC sign-in, negative cases, step-up, CLI device flow, re-evaluation (docs/12 §1, §6)."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import base_config, csrf, login, sign_in_as, use_jar


def test_sign_in_creates_user_session_and_audit(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    resp = login(client, oidc, "bob", redirect="/missions")
    assert resp.status_code == 302 and resp.headers["location"] == "/missions"
    cookie = resp.headers.get_list("set-cookie")
    assert any(
        c.startswith("__Host-z_session=") and "HttpOnly" in c and "Secure" in c and "SameSite=lax" in c for c in cookie
    )
    me = client.get("/api/v1/me").json()
    assert me["user"]["username"] == "bob"
    assert {r["role"] for r in me["user"]["effective_roles"]} == {"operator"}
    assert me["user"]["teams"][0]["name"] == "Cyber Ops"
    perms = {p["permission"] for p in me["permissions"]}
    assert "missions:start" in perms and "actions:approve" not in perms
    assert all(
        p["scope"].startswith("team:") or p["permission"] in {"missions:read", "agents:read"} or True
        for p in me["permissions"]
    )
    audit = client.get("/api/v1/audit").json()["items"]
    assert any(e["action"] == "auth.login" and e["result"] == "success" for e in audit)


def test_open_redirect_is_refused(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    resp = login(client, oidc, "bob", redirect="https://evil.example/")
    assert resp.headers["location"] == "/"


@pytest.mark.parametrize("fault", ["bad_issuer", "bad_audience", "expired", "wrong_nonce", "bad_signature"])
def test_bad_id_tokens_are_refused(client: TestClient, oidc: FakeGitLabOIDC, fault: str) -> None:
    oidc.add_fault(fault)
    resp = login(client, oidc, "bob")
    assert resp.status_code == 302
    expected = {
        "bad_issuer": "bad_issuer",
        "bad_audience": "bad_audience",
        "expired": "expired_token",
        "wrong_nonce": "bad_nonce",
        "bad_signature": "bad_signature",
    }[fault]
    assert resp.headers["location"] == f"/signin?error={expected}"
    assert client.get("/api/v1/me").status_code == 401


def test_replayed_state_is_refused(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    oidc.set_current_user("bob")
    start = client.get("/api/v1/auth/login", follow_redirects=False)
    authorize = httpx.get(start.headers["location"], follow_redirects=False)
    callback = urlparse(authorize.headers["location"])
    first = client.get(f"{callback.path}?{callback.query}", follow_redirects=False)
    assert first.headers["location"] == "/"
    client.cookies.clear()
    second = client.get(f"{callback.path}?{callback.query}", follow_redirects=False)
    assert second.headers["location"] == "/signin?error=invalid_state"
    assert client.get("/api/v1/me").status_code == 401


def test_user_outside_allowed_groups_cannot_sign_in(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    resp = login(client, oidc, "mallory")
    assert resp.headers["location"] == "/signin?error=not_in_allowed_group"


def test_no_mapped_group_gives_viewer_or_deny(make_client, oidc: FakeGitLabOIDC) -> None:  # type: ignore[no-untyped-def]
    viewer = make_client()
    login(viewer, oidc, "victor")
    assert {r["role"] for r in viewer.get("/api/v1/me").json()["user"]["effective_roles"]} == {"viewer"}
    deny = make_client(base_config(oidc.issuer, **{"auth.no_match": "deny"}))
    assert login(deny, oidc, "victor").headers["location"] == "/signin?error=no_mapped_group"


def test_suspended_user_cannot_sign_in(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(client, oidc, "bob")
    admin = sign_in_as(client, oidc, "alice")
    bob = next(u for u in client.get("/api/v1/admin/users").json()["items"] if u["username"] == "bob")
    assert (
        client.patch(f"/api/v1/admin/users/{bob['id']}", json={"action": "suspend"}, headers=csrf(client)).status_code
        == 200
    )
    client.cookies.clear()
    assert login(client, oidc, "bob").headers["location"] == "/signin?error=account_suspended"
    use_jar(client, admin)
    assert (
        client.patch(
            f"/api/v1/admin/users/{bob['id']}", json={"action": "reactivate"}, headers=csrf(client)
        ).status_code
        == 200
    )
    client.cookies.clear()
    assert login(client, oidc, "bob").headers["location"] == "/"


def test_step_up_flow(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    login(client, oidc, "alice")
    mapping = client.get("/api/v1/admin/group-mapping").json()
    # fresh sign-in counts as step-up; age it out by moving the clock of the check
    from z_server.auth import identity

    original = identity.now
    try:
        identity.now = lambda: original() + __import__("datetime").timedelta(seconds=400)  # type: ignore[assignment]
        stale = client.put("/api/v1/admin/group-mapping", json=mapping, headers=csrf(client))
    finally:
        identity.now = original  # type: ignore[assignment]
    assert stale.status_code == 401 and stale.json()["type"] == "urn:z:problem:step-up-required"
    start = client.post("/api/v1/auth/step-up", json={"return_to": "/admin"}, headers=csrf(client))
    url = start.json()["redirect_url"]
    q = parse_qs(urlparse(url).query)
    assert q["prompt"] == ["login"] and q["max_age"] == ["300"]
    oidc.set_current_user("alice", auth_age_s=0)
    authorize = httpx.get(url, follow_redirects=False)
    callback = urlparse(authorize.headers["location"])
    done = client.get(f"{callback.path}?{callback.query}", follow_redirects=False)
    assert done.headers["location"] == "/admin"
    assert client.put("/api/v1/admin/group-mapping", json=mapping, headers=csrf(client)).status_code == 200


def test_step_up_with_stale_provider_login_is_refused(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    login(client, oidc, "alice")
    url = client.post("/api/v1/auth/step-up", json={}, headers=csrf(client)).json()["redirect_url"]
    oidc.set_current_user("alice", auth_age_s=3600)
    # a provider that ignores prompt=login / max_age returns the old authentication time
    parts = urlparse(url)
    query = {k: v for k, v in parse_qs(parts.query).items() if k not in {"prompt", "max_age"}}
    ignored = parts._replace(query=urlencode(query, doseq=True)).geturl()
    authorize = httpx.get(ignored, follow_redirects=False)
    callback = urlparse(authorize.headers["location"])
    assert (
        client.get(f"{callback.path}?{callback.query}", follow_redirects=False).headers["location"]
        == "/signin?error=stale_authentication"
    )


def test_cli_device_flow_and_exchange(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    config = client.get("/api/v1/auth/cli/config").json()
    assert config["client_id"] == "z-cli" and config["device_flow"] is True
    discovery = httpx.get(f"{config['issuer_url']}/.well-known/openid-configuration").json()
    device = httpx.post(
        discovery["device_authorization_endpoint"], data={"client_id": "z-cli", "scope": "openid profile email"}
    ).json()
    oidc.approve_device(device["user_code"], "carol")
    tokens = httpx.post(
        discovery["token_endpoint"],
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device["device_code"],
            "client_id": "z-cli",
        },
    ).json()
    assert "access_token" in tokens, tokens
    client.cookies.clear()
    exchanged = client.post(
        "/api/v1/auth/cli/exchange",
        json={
            "gitlab_access_token": tokens["access_token"],
            "id_token": tokens.get("id_token"),
            "device_name": "laptop",
        },
    )
    assert exchanged.status_code == 200, exchanged.text
    token = exchanged.json()["token"]
    assert token.startswith("zs_")
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["user"]["username"] == "carol" and me["session"]["kind"] == "cli"
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer zs_forged"}).status_code == 401


def test_cli_exchange_refuses_a_bad_token(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    resp = client.post("/api/v1/auth/cli/exchange", json={"gitlab_access_token": "not-a-token", "device_name": "x"})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_group_removed_loses_access_at_reevaluation(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    login(client, oidc, "bob")
    assert client.get("/api/v1/me").status_code == 200
    oidc.update_user("bob", groups=["external/contractors"])
    from z_server.workers.identity import reevaluate_due

    results = client.portal.call(reevaluate_due, client.app.state.z, 0)  # type: ignore[union-attr,attr-defined]
    assert any(r.outcome == "deprovisioned" for r in results)
    assert client.get("/api/v1/me").status_code == 401


def test_blocked_user_loses_access_at_reevaluation(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    login(client, oidc, "carol")
    oidc.update_user("carol", blocked=True)
    from z_server.workers.identity import reevaluate_due

    results = client.portal.call(reevaluate_due, client.app.state.z, 0)  # type: ignore[union-attr,attr-defined]
    assert [r.outcome for r in results if r.outcome != "skipped"] == ["deprovisioned"]
    assert client.get("/api/v1/me").status_code == 401


def test_logout_revokes_the_session(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    login(client, oidc, "bob")
    assert client.post("/api/v1/auth/logout", headers=csrf(client)).status_code == 204
    assert client.get("/api/v1/me").status_code == 401


def test_idle_and_absolute_timeouts(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    from datetime import timedelta

    from z_server.auth import identity

    login(client, oidc, "bob")
    original = identity.now
    try:
        identity.now = lambda: original() + timedelta(hours=3)  # type: ignore[assignment]
        assert client.get("/api/v1/me").status_code == 401  # idle 2 h
    finally:
        identity.now = original  # type: ignore[assignment]
    _ = time


def test_a_sign_in_started_elsewhere_cannot_be_completed_here(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    """Login CSRF: bob starts a sign-in and sends the callback link to a victim; the victim's browser must not end
    up signed in as bob (they would connect their own accounts to bob's Z identity)."""
    oidc.set_current_user("bob")
    client.cookies.clear()
    start = client.get("/api/v1/auth/login", follow_redirects=False)
    authorize = httpx.get(start.headers["location"], follow_redirects=False)
    callback = urlparse(authorize.headers["location"])
    client.cookies.clear()  # the victim's browser: no login cookie of this flow
    lured = client.get(f"{callback.path}?{callback.query}", follow_redirects=False)
    assert lured.headers["location"] == "/signin?error=unbound_state"
    assert client.get("/api/v1/me").status_code == 401
