"""Helpers for the compose e2e tests (marker e2e): the z-server API over HTTP, the fake LLM and fake tools control
ports, the local test accounts of compose.e2e.yml and the database owner through the postgres container.

Every request the shared `api` client (and the test-account clients) sends to z-server carries its own
X-Request-Id; mutating ones are recorded in RECORDED so test_compose_audit.py can check that each has an audit event."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

Z = os.environ.get("Z_E2E_URL", "http://127.0.0.1:8000")  # z-server (the web app is z-ui on :8080)
LLM = os.environ.get("Z_E2E_LLM_URL", "http://127.0.0.1:8090")
TOOLS = os.environ.get("Z_E2E_TOOLS_URL", "http://127.0.0.1:8091")
COMPOSE_PROJECT = os.environ.get("Z_E2E_COMPOSE_PROJECT", "z")  # `name:` of deploy/compose/docker-compose.yml
API = f"{Z}/api/v1"

# compose.e2e.yml: Z_TEST_ACCOUNT_PASSWORDS for the test accounts of config/z.example.yaml (team Cyber Ops)
TEST_PASSWORDS = {"tester": "e2e-tester-e2e-tester", "tester-approver": "e2e-approver-e2e-approver"}

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "budget_exceeded"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    request_id: str
    status: int


RECORDED: list[Call] = []  # every mutating z-server call of this pytest run, in order


def _tag(request: httpx.Request) -> None:
    request.headers.setdefault("X-Request-Id", uuid.uuid4().hex)


def _record(response: httpx.Response) -> None:
    request = response.request
    if request.method in MUTATING:
        RECORDED.append(Call(request.method, request.url.path, request.headers["X-Request-Id"], response.status_code))


def client(headers: dict[str, str] | None = None) -> httpx.Client:
    """A z-server API client whose calls are tagged and recorded. No credentials: in auth mode dev the server
    signs such requests in as the dev user (an org admin)."""
    return httpx.Client(
        base_url=API, timeout=30, headers=headers or {}, event_hooks={"request": [_tag], "response": [_record]}
    )


def sign_in_local(username: str) -> httpx.Client:
    """Local sign-in of a test account: a web session (fresh step-up), sent as cookies with the CSRF header.
    The cookies are `Secure`, so they are set by hand on this plain-HTTP connection."""
    with client() as anon:
        resp = anon.post("/auth/local/login", json={"username": username, "password": TEST_PASSWORDS[username]})
    assert resp.status_code == 200, resp.text
    cookies = {c.name: c.value for c in resp.cookies.jar}
    cookie = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return client({"Cookie": cookie, "X-CSRF-Token": resp.json()["csrf_token"]})


def step(name: str, arguments: dict[str, Any], *, delay_ms: int = 0) -> dict[str, Any]:
    """One fake model turn that calls a tool."""
    turn: dict[str, Any] = {"tool_calls": [{"name": name, "arguments": arguments}]}
    return {"delay_ms": delay_ms, "then": turn} if delay_ms else turn


def finish(message: str | dict[str, Any] = "done") -> dict[str, Any]:
    return step("finish", {"message": message if isinstance(message, str) else json.dumps(message)})


class FakeLLM:
    """Control port of tests/fakes/fake_llm (behind LiteLLM in the stack)."""

    def reset(self) -> None:
        httpx.post(f"{LLM}/_fake/reset", timeout=10).raise_for_status()

    def rules(self, *rules: dict[str, Any]) -> None:
        """Later rules win; every rule here only answers the agents' calls (the requests that carry tools)."""
        for rule in rules:
            body = {**rule, "match": {"has_tools": True, **rule.get("match", {})}}
            httpx.post(f"{LLM}/_fake/rules", json=body, timeout=10).raise_for_status()

    def requests(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = httpx.get(f"{LLM}/_fake/requests", timeout=10).json()
        return out


class FakeTools:
    """Control port of tests/fakes/fake_tools (Jira, Logos, DefectDojo, CMDB, ... behind the tool gateway)."""

    def reset(self) -> None:
        httpx.post(f"{TOOLS}/_fake/reset", timeout=10).raise_for_status()

    def state(self) -> dict[str, Any]:
        out: dict[str, Any] = httpx.get(f"{TOOLS}/_fake/state", timeout=10).json()
        return out

    def writes(self, kind: str) -> list[dict[str, Any]]:
        return [w for w in self.state()["writes"] if w["type"] == kind]


def mission(api: httpx.Client, mission_id: str) -> dict[str, Any]:
    resp = api.get(f"/missions/{mission_id}")
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def jobs(api: httpx.Client, mission_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 200, **({"cursor": cursor} if cursor else {})}
        page = api.get(f"/missions/{mission_id}/jobs", params=params).json()
        items += page["items"]
        cursor = page.get("next_cursor")
        if not cursor:
            return items


def events(api: httpx.Client, mission_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    replay = api.get(f"/missions/{mission_id}/replay").json()
    return [e for e in replay["events"] if kind is None or e["type"] == kind]


def wait_for(what: str, probe: Any, timeout_s: float = 180, every_s: float = 1.0) -> Any:
    """Poll `probe()` until it returns something truthy."""
    deadline = time.monotonic() + timeout_s
    while True:
        value = probe()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(every_s)


def wait_status(api: httpx.Client, mission_id: str, until: set[str], timeout_s: float = 240) -> dict[str, Any]:
    last: dict[str, Any] = {}

    def probe() -> dict[str, Any] | None:
        nonlocal last
        last = mission(api, mission_id)
        return last if last["status"] in until else None

    try:
        return dict(wait_for(f"mission {mission_id} in {sorted(until)}", probe, timeout_s))
    except AssertionError:
        raise AssertionError(f"mission still {last.get('status')}: {last}, jobs {jobs(api, mission_id)}") from None


def approve_exposure(api: httpx.Client, connector: str) -> None:
    """Admin review of a pack connector: expose what the pack proposes (destructive operations stay unexposed)."""
    ops = api.get(f"/connectors/{connector}/operations").json()
    resp = api.post(
        f"/connectors/{connector}/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
    )
    assert resp.status_code == 200, resp.text


def compose_container(service: str) -> str:
    out = subprocess.run(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={COMPOSE_PROJECT}",
            "--filter",
            f"label=com.docker.compose.service={service}",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert out, f"no running {service} container in compose project {COMPOSE_PROJECT}"
    return out[0]


def psql_owner(sql: str) -> str:
    """Run SQL as the database owner `z` inside the postgres container (local socket). Only for what the
    application role cannot do on purpose, such as rewriting the append-only audit table."""
    psql = ["psql", "-U", "z", "-d", "z", "-v", "ON_ERROR_STOP=1", "-At", "-c", sql]
    done = subprocess.run(
        ["docker", "exec", compose_container("postgres"), *psql],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def z_cli(config_dir: str) -> Any:
    """`z` from source against the stack, as the dev user: `z login` in auth mode dev, a token file in
    `config_dir`. Returns a function that runs one command and returns the finished process."""
    env = {**os.environ, "Z_URL": Z, "Z_CONFIG_DIR": config_dir, "NO_COLOR": "1"}
    env.pop("Z_TOKEN", None)

    def run(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "z_cli", *args], env=env, capture_output=True, text=True, timeout=timeout
        )

    login = run("login")
    assert login.returncode == 0, login.stdout + login.stderr
    return run
