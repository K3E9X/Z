"""z login (device flow, PKCE fallback, dev mode, API tokens), whoami, logout, token storage, auth errors."""

from __future__ import annotations

import json
import stat
import threading
import webbrowser
from pathlib import Path
from typing import Any

import httpx
import pytest

from cli.tests.harness import LiveServer, Z, plain
from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC


def test_device_flow_login_whoami_logout(z: Z, live: LiveServer) -> None:
    result = z.login("bob")
    out = plain(result.output)
    assert "enter the code" in out and "Signed in to" in out and "bob" in out

    token_file = z.home / "token"
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600  # docs/10 §4 fallback store
    token = z.token()
    assert token.startswith("zs_")

    who = z("whoami", "--json")
    assert who.exit_code == 0, who.output
    me = json.loads(who.output)
    assert me["user"]["username"] == "bob"
    assert me["session"]["kind"] == "cli"
    assert {"role": "operator"}.items() <= me["user"]["effective_roles"][0].items()
    assert "csrf_token" not in me
    text = plain(z("whoami").output)
    assert "Bob Durand" in text and "Cyber Ops" in text and "operator" in text

    # the CLI session is listed with its device name
    sessions = json.loads(z("sessions", "list", "--json").output)
    assert any(s["kind"] == "cli" and (s.get("device") or "").startswith("z-cli") for s in sessions)

    out = z("logout")
    assert out.exit_code == 0 and "Signed out" in plain(out.output)
    assert not token_file.exists()
    with live.http(token) as http:
        assert http.get("/api/v1/me").status_code == 401  # the server ended the session

    again = z("whoami")
    assert again.exit_code == 4
    assert "Not signed in" in plain(again.output)


def test_denied_device_code_is_an_auth_error(z: Z, oidc: FakeGitLabOIDC) -> None:
    def deny(url: str, *a: Any, **k: Any) -> bool:
        from urllib.parse import parse_qs, urlparse

        oidc.deny_device(parse_qs(urlparse(url).query)["user_code"][0])
        return True

    original = webbrowser.open
    webbrowser.open = deny  # type: ignore[assignment]
    try:
        result = z("login")
    finally:
        webbrowser.open = original  # type: ignore[assignment]
    assert result.exit_code == 4
    assert "denied" in plain(result.output)


def test_pkce_loopback_fallback(z: Z, oidc: FakeGitLabOIDC, monkeypatch: pytest.MonkeyPatch) -> None:
    """Older GitLab (no device grant): authorization code + PKCE on the fixed loopback redirect."""
    oidc.set_current_user("carol")
    seen: list[str] = []

    def browser(url: str, *a: Any, **k: Any) -> bool:
        seen.append(url)

        def follow() -> None:  # the browser: GitLab authorizes, then redirects to the CLI's loopback
            resp = httpx.get(url, follow_redirects=False)
            httpx.get(resp.headers["location"])

        threading.Thread(target=follow, daemon=True).start()
        return True

    monkeypatch.setattr(webbrowser, "open", browser)
    result = z("login", "--pkce")
    assert result.exit_code == 0, result.output
    assert (
        "code_challenge_method=S256" in seen[0] and "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fcallback" in seen[0]
    )
    me = json.loads(z("whoami", "--json").output)
    assert me["user"]["username"] == "carol"


def test_api_token_login_and_scopes(z: Z) -> None:
    bob = z.as_user("bob")
    created = bob("tokens", "create", "ci", "--scope", "missions:read", "--days", "7", "--json")
    assert created.exit_code == 0, created.output
    token = json.loads(created.output)["token"]
    assert token.startswith("zt_")

    ci = Z(z.url, z.home / "ci")
    login = ci("login", "--with-token", input=token + "\n")
    assert login.exit_code == 0, login.output
    assert json.loads(ci("whoami", "--json").output)["user"]["username"] == "bob"
    assert ci("missions", "list", "--json").exit_code == 0
    # outside the token's scopes: refused with the auth exit code and the missing permission
    denied = ci("run", "summarize-file", "--input", "file=x.md")
    assert denied.exit_code == 4
    assert "Not allowed" in plain(denied.output)

    listed = json.loads(bob("tokens", "list", "--json").output)
    assert [t["name"] for t in listed] == ["ci"] and "token" not in listed[0]
    tid = listed[0]["id"]
    assert bob("tokens", "revoke", tid, "--yes").exit_code == 0
    expired = ci("whoami")
    assert expired.exit_code == 4 and "expired or was revoked" in plain(expired.output)


def test_z_token_env_overrides_the_store(z: Z) -> None:
    bob = z.as_user("bob")
    token = bob.token()
    other = Z(z.url, z.home / "env-only")
    result = other("whoami", "--json", env={"Z_TOKEN": token})
    assert result.exit_code == 0 and json.loads(result.output)["user"]["username"] == "bob"


def test_dev_mode_login(zdev: Z) -> None:
    me = json.loads(zdev("whoami", "--json").output)
    assert me["user"]["local_kind"] == "dev"
    assert me["session"]["kind"] == "cli"


def test_keyring_store_is_preferred(z: Z, monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring
    from keyring.backend import KeyringBackend

    class Memory(KeyringBackend):
        priority = 10  # type: ignore[assignment]
        store: dict[tuple[str, str], str] = {}

        def get_password(self, service: str, username: str) -> str | None:
            return self.store.get((service, username))

        def set_password(self, service: str, username: str, password: str) -> None:
            self.store[(service, username)] = password

        def delete_password(self, service: str, username: str) -> None:
            self.store.pop((service, username), None)

    previous = keyring.get_keyring()
    keyring.set_keyring(Memory())
    try:
        z.env["Z_KEYRING"] = None
        result = z.login("bob")
        assert "OS keyring" in plain(result.output)
        assert not (z.home / "token").exists()
        assert Memory.store[("z-cli", z.url)].startswith("zs_")
        assert z("whoami").exit_code == 0
        assert z("logout").exit_code == 0
        assert ("z-cli", z.url) not in Memory.store
    finally:
        keyring.set_keyring(previous)


def test_no_server_configured_is_a_usage_error(tmp_path: Path) -> None:
    cli = Z("", tmp_path)
    result = cli("whoami", env={"Z_URL": None})
    assert result.exit_code == 2
    assert "No Z server configured" in plain(result.output)


def test_unreachable_server(tmp_path: Path) -> None:
    cli = Z("http://127.0.0.1:9", tmp_path)
    result = cli("whoami", env={"Z_TOKEN": "zs_x"})
    assert result.exit_code == 1
    assert "Cannot reach Z" in plain(result.output)
