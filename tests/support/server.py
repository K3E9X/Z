"""Helpers shared by the server tests (importable: tests/ is on the pytest pythonpath)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi.testclient import TestClient
from z_common.config import load_config

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC

REPO = Path(__file__).resolve().parents[2]
SECRET = "test-secret-key-0123456789abcdef-0123456789"


def base_config(issuer: str, **overrides: Any) -> dict[str, Any]:
    env = {
        "OIDC_ISSUER_URL": issuer,
        "OIDC_CLIENT_ID": "z-web",
        "OIDC_CLIENT_SECRET": "z-web-secret",
        "LITELLM_URL": "http://litellm:4000",
        "Z_AUTH_MODE": "oidc",
    }
    config = load_config(REPO / "config/z.example.yaml", env)
    config["auth"]["cli_client_id"] = "z-cli"
    for dotted, value in overrides.items():
        target = config
        *path, last = dotted.split(".")
        for key in path:
            target = target.setdefault(key, {})
        target[last] = value
    return config


def login(client: TestClient, oidc: FakeGitLabOIDC, username: str, redirect: str = "/") -> httpx.Response:
    """Full browser flow: Z -> GitLab authorize -> Z callback. Returns the callback response."""
    oidc.set_current_user(username)
    start = client.get("/api/v1/auth/login", params={"redirect": redirect}, follow_redirects=False)
    assert start.status_code == 302, start.text
    authorize = httpx.get(start.headers["location"], follow_redirects=False)
    assert authorize.status_code in (302, 303), authorize.text
    callback = urlparse(authorize.headers["location"])
    assert callback.netloc == "testserver", callback
    return client.get(f"{callback.path}?{callback.query}", follow_redirects=False)


def csrf(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("__Host-z_csrf") or ""}


def sign_in_as(client: TestClient, oidc: FakeGitLabOIDC, username: str) -> dict[str, str]:
    """Sign in on a clean cookie jar and return the jar, to switch users with `use_jar`."""
    client.cookies.clear()
    resp = login(client, oidc, username)
    assert resp.status_code == 302 and not resp.headers["location"].startswith("/signin"), resp.headers.get("location")
    return dict(client.cookies)


def use_jar(client: TestClient, jar: dict[str, str]) -> None:
    client.cookies.clear()
    for name, value in jar.items():
        client.cookies.set(name, value)
