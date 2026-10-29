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
    config["judge"]["model_stage"] = False  # tests that exercise the judge model turn it on with a fake LLM
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


def seed_packs(client: TestClient, packs_root: Path | None = None, only: set[str] | None = None) -> Any:
    """Run the pack seed inside the app's event loop."""
    from z_server import audit
    from z_server.catalog.seed import seed

    state = client.app.state.z  # type: ignore[attr-defined]

    async def run() -> Any:
        async with state.sessions() as db, db.begin():
            return await seed(db, packs_root or REPO / "packs", audit.Actor("system:test"), only)

    return client.portal.call(run)  # type: ignore[union-attr]


class ContractViolation(AssertionError):
    pass


def contract_checker(app: Any) -> Any:
    """httpx response hook: every JSON response of a contract route must match openapi.yaml, and every
    error must be RFC 9457 problem details. Turns any drift between code and contract into a test failure."""
    from starlette.routing import Match
    from z_common.contracts import errors as schema_errors
    from z_common.contracts import response_errors

    def check(response: httpx.Response) -> None:
        request = response.request
        scope = {"type": "http", "path": request.url.path, "method": request.method, "root_path": ""}
        op_id = None
        for route in app.routes:
            match, _ = route.matches(scope)
            if match == Match.FULL:
                op_id = getattr(route, "operation_id", None)
                break
        if op_id is None:
            return
        response.read()
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            assert content_type.startswith("application/problem+json"), (
                f"{op_id} {response.status_code}: not problem+json"
            )
            problems = schema_errors("common.schema.json", response.json(), "/$defs/Problem")
            if problems:
                raise ContractViolation(f"{op_id} {response.status_code} problem: {problems}")
            return
        if not content_type.startswith("application/json"):
            return
        found = response_errors(op_id, response.status_code, response.json())
        if found is None:
            raise ContractViolation(f"{op_id}: status {response.status_code} is not declared in the contract")
        if found:
            raise ContractViolation(f"{op_id} {response.status_code}: {found[:5]}")

    return check
