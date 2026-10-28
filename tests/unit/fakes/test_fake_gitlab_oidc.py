from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest

from tests.fakes.fake_gitlab_oidc import DEVICE_GRANT, FAULTS, FakeGitLabOIDC

WEB = ("z-web", "z-web-secret")
REDIRECT = "http://127.0.0.1:8000/api/v1/auth/callback"


@pytest.fixture(scope="module")
def idp() -> Iterator[FakeGitLabOIDC]:
    with FakeGitLabOIDC() as server:
        yield server


@pytest.fixture(autouse=True)
def _reset(idp: FakeGitLabOIDC) -> None:
    idp.reset()


def validate(idp: FakeGitLabOIDC, token: str, nonce: str, audience: str = WEB[0]) -> dict[str, Any]:
    """A correct validator: issuer, audience, signature from JWKS, expiry, nonce."""
    conf = httpx.get(f"{idp.url}/.well-known/openid-configuration").json()
    jwks = jwt.PyJWKClient(conf["jwks_uri"])
    key = jwks.get_signing_key_from_jwt(token)
    claims: dict[str, Any] = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=conf["issuer"],
        options={"require": ["exp", "iat", "sub"]},
    )
    if claims.get("nonce") != nonce:
        raise jwt.InvalidTokenError("nonce mismatch")
    return claims


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def login(idp: FakeGitLabOIDC, user: str = "alice", **extra: str) -> tuple[dict[str, Any], str]:
    verifier, challenge = pkce()
    nonce = secrets.token_hex(8)
    params = {
        "client_id": WEB[0],
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "openid profile email",
        "state": "st-1",
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "login_hint": user,
        **extra,
    }
    r = httpx.get(f"{idp.url}/oauth/authorize", params=params)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"] == ["st-1"]
    form = {
        "grant_type": "authorization_code",
        "code": q["code"][0],
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
    }
    tok = httpx.post(f"{idp.url}/oauth/token", data=form, auth=WEB)
    assert tok.status_code == 200, tok.text
    return tok.json(), nonce


def test_discovery_and_jwks(idp: FakeGitLabOIDC) -> None:
    conf = httpx.get(f"{idp.url}/.well-known/openid-configuration").json()
    assert conf["issuer"] == idp.url
    assert conf["jwks_uri"] == f"{idp.url}/oauth/discovery/keys"
    assert conf["device_authorization_endpoint"].endswith("/oauth/authorize_device")
    key = httpx.get(conf["jwks_uri"]).json()["keys"][0]
    assert key["kty"] == "RSA" and key["kid"] and key["alg"] == "RS256"


def test_code_flow_with_pkce_and_userinfo(idp: FakeGitLabOIDC) -> None:
    tokens, nonce = login(idp, "bob")
    claims = validate(idp, tokens["id_token"], nonce)
    assert claims["preferred_username"] == "bob" and claims["email"] == "bob@example.test"
    assert claims["groups_direct"] == ["cyber/z/z-operators", "cyber/soc-csirt"]
    assert claims["auth_time"] <= claims["iat"]
    info = httpx.get(f"{idp.url}/oauth/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert info["sub"] == claims["sub"]
    assert set(info["groups"]) == {"cyber/z/z-operators", "cyber/soc-csirt"}
    assert "https://gitlab.org/claims/groups/developer" in info


def test_pkce_mismatch_and_code_reuse(idp: FakeGitLabOIDC) -> None:
    _verifier, challenge = pkce()
    params = {
        "client_id": WEB[0],
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": "openid",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "login_hint": "alice",
    }
    code = parse_qs(urlparse(httpx.get(f"{idp.url}/oauth/authorize", params=params).headers["location"]).query)
    form = {
        "grant_type": "authorization_code",
        "code": code["code"][0],
        "redirect_uri": REDIRECT,
        "code_verifier": "wrong",
    }
    assert httpx.post(f"{idp.url}/oauth/token", data=form, auth=WEB).json()["error"] == "invalid_grant"
    assert (
        httpx.post(f"{idp.url}/oauth/token", data={**form, "client_secret": "nope", "client_id": WEB[0]}).status_code
        == 401
    )
    bad_redirect = httpx.get(f"{idp.url}/oauth/authorize", params={**params, "redirect_uri": "https://evil.example/cb"})
    assert bad_redirect.status_code == 400


def test_client_secret_post_and_refresh(idp: FakeGitLabOIDC) -> None:
    tokens, _ = login(idp)
    form = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": WEB[0],
        "client_secret": WEB[1],
    }
    again = httpx.post(f"{idp.url}/oauth/token", data=form)
    assert again.status_code == 200 and again.json()["refresh_token"] != tokens["refresh_token"]
    assert httpx.post(f"{idp.url}/oauth/token", data=form).json()["error"] == "invalid_grant"  # rotated


@pytest.mark.parametrize("fault", FAULTS)
def test_each_fault_is_rejected_by_a_correct_validator(idp: FakeGitLabOIDC, fault: str) -> None:
    idp.add_fault(fault)
    tokens, nonce = login(idp)
    with pytest.raises(jwt.InvalidTokenError):
        validate(idp, tokens["id_token"], nonce)
    clean, nonce2 = login(idp)  # one-shot fault
    validate(idp, clean["id_token"], nonce2)


def test_sticky_fault_until_cleared(idp: FakeGitLabOIDC) -> None:
    idp.add_fault("bad_audience", sticky=True)
    for _ in range(2):
        tokens, nonce = login(idp)
        with pytest.raises(jwt.InvalidAudienceError):
            validate(idp, tokens["id_token"], nonce)
    idp.clear_faults()
    tokens, nonce = login(idp)
    validate(idp, tokens["id_token"], nonce)


def test_blocked_or_deleted_user_fails_refresh_and_userinfo(idp: FakeGitLabOIDC) -> None:
    tokens, _ = login(idp, "carol")
    idp.update_user("carol", blocked=True)
    form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
    refresh = httpx.post(f"{idp.url}/oauth/token", data=form, auth=WEB)
    assert refresh.status_code == 400 and refresh.json()["error"] == "invalid_grant"
    info = httpx.get(f"{idp.url}/oauth/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert info.status_code == 401
    blocked = httpx.get(
        f"{idp.url}/oauth/authorize",
        params={"client_id": WEB[0], "redirect_uri": REDIRECT, "response_type": "code", "login_hint": "carol"},
    )
    assert "error=access_denied" in blocked.headers["location"]

    tokens, _ = login(idp, "dave")
    idp.delete_user("dave")
    assert (
        httpx.post(
            f"{idp.url}/oauth/token", data={**form, "refresh_token": tokens["refresh_token"]}, auth=WEB
        ).status_code
        == 400
    )


def test_group_change_visible_in_userinfo(idp: FakeGitLabOIDC) -> None:
    idp.add_user("olga", ["cyber/z/z-operators"])
    tokens, _ = login(idp, "olga")
    idp.update_user("olga", groups=["cyber/z/z-approvers"])
    info = httpx.get(f"{idp.url}/oauth/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert "cyber/z/z-approvers" in info["groups"] and "cyber/z/z-operators" not in info["groups"]


def test_prompt_login_and_max_age_refresh_auth_time(idp: FakeGitLabOIDC) -> None:
    idp.set_current_user("alice", auth_age_s=3600)
    verifier, challenge = pkce()

    def auth_time(**extra: str) -> int:
        params = {
            "client_id": WEB[0],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "scope": "openid",
            "nonce": "n",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **extra,
        }
        loc = httpx.get(f"{idp.url}/oauth/authorize", params=params).headers["location"]
        code = parse_qs(urlparse(loc).query)["code"][0]
        tok = httpx.post(
            f"{idp.url}/oauth/token",
            auth=WEB,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": verifier,
            },
        )
        return int(validate(idp, tok.json()["id_token"], "n")["auth_time"])

    old = auth_time()
    assert time.time() - old >= 3500
    assert auth_time(max_age="300") > old + 3000
    idp.set_current_user("alice", auth_age_s=1000)
    assert time.time() - auth_time(prompt="login") < 60
    idp.set_current_user(None)
    params = {"client_id": WEB[0], "redirect_uri": REDIRECT, "response_type": "code"}
    assert "error=login_required" in httpx.get(f"{idp.url}/oauth/authorize", params=params).headers["location"]


def test_device_flow(idp: FakeGitLabOIDC) -> None:
    idp.configure(device_interval=0)
    start = httpx.post(f"{idp.url}/oauth/authorize_device", data={"client_id": "z-cli", "scope": "openid profile"})
    d = start.json()
    assert {
        "device_code",
        "user_code",
        "verification_uri",
        "verification_uri_complete",
        "interval",
        "expires_in",
    } <= set(d)
    poll = {"grant_type": DEVICE_GRANT, "device_code": d["device_code"], "client_id": "z-cli"}
    assert httpx.post(f"{idp.url}/oauth/token", data=poll).json()["error"] == "authorization_pending"
    idp.approve_device(d["user_code"], "erin")
    tokens = httpx.post(f"{idp.url}/oauth/token", data=poll).json()
    claims = jwt.decode(tokens["id_token"], options={"verify_signature": False})
    assert claims["preferred_username"] == "erin" and "nonce" not in claims
    assert httpx.post(f"{idp.url}/oauth/token", data=poll).json()["error"] == "invalid_grant"


def test_device_flow_slow_down_denied_expired(idp: FakeGitLabOIDC) -> None:
    idp.configure(device_interval=30)
    d = httpx.post(f"{idp.url}/oauth/authorize_device", data={"client_id": "z-cli"}).json()
    poll = {"grant_type": DEVICE_GRANT, "device_code": d["device_code"], "client_id": "z-cli"}
    assert httpx.post(f"{idp.url}/oauth/token", data=poll).json()["error"] == "authorization_pending"
    assert httpx.post(f"{idp.url}/oauth/token", data=poll).json()["error"] == "slow_down"
    idp.deny_device(d["user_code"])
    assert httpx.post(f"{idp.url}/oauth/token", data=poll).json()["error"] == "access_denied"
    idp.configure(device_expires_in=0)
    d2 = httpx.post(f"{idp.url}/oauth/authorize_device", data={"client_id": "z-cli"}).json()
    poll["device_code"] = d2["device_code"]
    assert httpx.post(f"{idp.url}/oauth/token", data=poll).json()["error"] == "expired_token"


def test_revoke(idp: FakeGitLabOIDC) -> None:
    tokens, _ = login(idp)
    assert httpx.post(f"{idp.url}/oauth/revoke", data={"token": tokens["access_token"]}, auth=WEB).status_code == 200
    info = httpx.get(f"{idp.url}/oauth/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert info.status_code == 401


def test_inherited_groups_in_userinfo(idp: FakeGitLabOIDC) -> None:
    idp.add_user("pat", ["cyber/z"], roles={"cyber/z": "owner"})
    tokens, nonce = login(idp, "pat")
    assert validate(idp, tokens["id_token"], nonce)["groups_direct"] == ["cyber/z"]
    info = httpx.get(f"{idp.url}/oauth/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert {"cyber/z", "cyber/z/z-admins", "cyber/z/z-operators"} <= set(info["groups"])
    assert "cyber/soc-csirt" not in info["groups"]
    assert info["https://gitlab.org/claims/groups/owner"] == ["z"]
