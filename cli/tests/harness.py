"""Test harness: the real z-server app under uvicorn (real HTTP and SSE), the CLI in-process, the runner side."""

from __future__ import annotations

import asyncio
import re
import socket
import threading
import time
import webbrowser
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import uvicorn
from typer.testing import CliRunner, Result
from z_server.app import create_app
from z_server.settings import Settings

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner
from tests.support.server import REPO, SECRET

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class LiveServer:
    """The z-server ASGI app under uvicorn on its own event loop thread."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = int(self.sock.getsockname()[1])
        self.loop = asyncio.new_event_loop()
        config = uvicorn.Config(app, log_level="warning", lifespan="on", ws="none", timeout_graceful_shutdown=1)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.server.serve(sockets=[self.sock]))

    def start(self) -> LiveServer:
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("z-server did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(15)
        self.sock.close()

    def call(self, fn: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        """Run a coroutine on the server's loop (its database engine lives there)."""
        return asyncio.run_coroutine_threadsafe(fn(), self.loop).result(30)

    @property
    def state(self) -> Any:
        return self.app.state.z

    def http(self, token: str | None = None) -> httpx.Client:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.Client(base_url=self.url, headers=headers, timeout=30)

    # ---------------------------------------------------------------- server-side helpers

    def seed(self) -> Any:
        from z_server import audit
        from z_server.catalog.seed import seed

        async def run() -> Any:
            async with self.state.sessions() as db, db.begin():
                return await seed(db, REPO / "packs", audit.Actor("system:test"), None)

        return self.call(run)

    def dispatch(self, executor: RecordingExecutor) -> list[str]:
        from z_server.scheduler.queue import dispatch

        async def run() -> list[str]:
            return list(await dispatch(self.state, executor, "test-worker"))

        return list(self.call(run))

    def fresh_step_up(self, username: str) -> None:
        """What a step-up re-authentication records on the user's sessions (CLI sessions cannot do one yet)."""
        from sqlalchemy import select, update
        from z_server.db.models import AuthSession, User

        async def run() -> None:
            async with self.state.sessions() as db, db.begin():
                ids = {u.id for u in (await db.execute(select(User).where(User.username == username))).scalars()}
                await db.execute(
                    update(AuthSession).where(AuthSession.user_id.in_(ids)).values(step_up_at=datetime.now(UTC))
                )

        self.call(run)

    def set_four_eyes(self, mission_id: str) -> None:
        from z_server.db.models import Mission

        async def run() -> None:
            async with self.state.sessions() as db, db.begin():
                mission = await db.get(Mission, mission_id)
                assert mission is not None
                mission.four_eyes = True

        self.call(run)


def serve(database_url: str, config: dict[str, Any], tmp: Path) -> LiveServer:
    settings = Settings(
        database_url=database_url,
        secret_key=SECRET,
        public_url="http://127.0.0.1",
        config=config,
        oidc_client_secret="z-web-secret",
        artifacts_dir=tmp / "artifacts",
    )
    app = create_app(settings, llm_keys=RecordingKeyAdmin(), workers=False)
    return LiveServer(app).start()


# ---------------------------------------------------------------- the CLI


@dataclass
class Z:
    """Runs `z` in-process with an isolated config directory (no keyring: the 0600 file store)."""

    url: str
    home: Path
    oidc: FakeGitLabOIDC | None = None
    env: dict[str, str | None] = field(default_factory=dict)
    runner: CliRunner = field(default_factory=CliRunner)

    def __call__(self, *args: str, input: str | None = None, env: dict[str, str | None] | None = None) -> Result:
        from z_cli import output
        from z_cli.app import app

        output.reset()
        full_env: dict[str, str | None] = {
            "Z_URL": self.url,
            "Z_CONFIG_DIR": str(self.home),
            "Z_KEYRING": "off",
            "COLUMNS": "220",
            "NO_COLOR": "1",
            "Z_TOKEN": None,
            **self.env,
            **(env or {}),
        }
        result = self.runner.invoke(app, list(args), input=input, env=full_env, catch_exceptions=False)
        output.reset()
        return result

    def as_user(self, username: str) -> Z:
        """A CLI signed in as `username` through the real device flow (fake GitLab approves the code)."""
        other = Z(self.url, self.home / username, self.oidc)
        other.login(username)
        return other

    def login(self, username: str) -> Result:
        assert self.oidc is not None
        oidc = self.oidc
        approved: list[str] = []

        def browser(url: str, *args: Any, **kwargs: Any) -> bool:
            code = parse_qs(urlparse(url).query).get("user_code", [""])[0]
            oidc.approve_device(code, username)
            approved.append(code)
            return True

        original = webbrowser.open
        webbrowser.open = browser  # type: ignore[assignment]
        try:
            result = self("login")
        finally:
            webbrowser.open = original  # type: ignore[assignment]
        assert result.exit_code == 0, result.output
        assert approved, "the CLI did not open the verification URL"
        return result

    def token(self) -> str:
        import json

        data = json.loads((self.home / "token").read_text())
        return str(data[self.url]["token"])


def plain(text: str) -> str:
    return ANSI.sub("", text)


# ---------------------------------------------------------------- the agent side


def now() -> str:
    return datetime.now(UTC).isoformat()


def runner_for(live: LiveServer, executor: RecordingExecutor, mission_id: str) -> ScriptedRunner:
    """Dispatch the mission's queued job and play the runner side with the real protocol."""
    launched = live.dispatch(executor)
    assert launched, "no job was dispatched"
    http = live.http()
    for job_id in launched:
        env = executor.launched[job_id]
        runner = ScriptedRunner(http, job_id, env["Z_JOB_TOKEN"])  # type: ignore[arg-type]
        spec = runner.get_spec()
        if spec.get("mission_id", mission_id) == mission_id:
            return runner
    raise AssertionError("no job of this mission was dispatched")


def expose(live: LiveServer, admin_token: str, connector: str, extra: dict[str, bool] | None = None) -> None:
    with live.http(admin_token) as http:
        ops = http.get(f"/api/v1/connectors/{connector}/operations").json()
        decisions = [{"id": o["id"], "exposed": (extra or {}).get(o["id"], o["proposed_exposed"])} for o in ops]
        resp = http.post(f"/api/v1/connectors/{connector}/approve-exposure", json={"operations": decisions})
        assert resp.status_code == 200, resp.text


ISSUE = {"project": "SEC", "summary": "Patch openssl on edge-01", "description": "CVE-2026-0001"}


def write_mission(live: LiveServer, token: str, writes: list[str]) -> str:
    plan = {
        "steps": [{"id": "act", "agent": "ticket-manager", "prompt": "Open the ticket", "writes": writes}],
        "writes": writes,
    }
    with live.http(token) as http:
        resp = http.post(
            "/api/v1/missions",
            json={"title": "Ticket", "prompt": "Open the ticket", "plan": plan, "connectors": ["jira", "logos"]},
        )
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])
