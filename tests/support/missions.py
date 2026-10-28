"""Test doubles for the mission slice: an executor that records launches, and a scripted runner that speaks the
real runner protocol (contracts/README.md "Runner protocol") against the app under test."""

from __future__ import annotations

import base64
import io
import tarfile
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi.testclient import TestClient

INTERNAL = "/api/v1/internal/jobs"


@dataclass
class RecordingExecutor:
    name: str = "recording"
    launched: dict[str, dict[str, str]] = field(default_factory=dict)
    stopped: list[str] = field(default_factory=list)
    alive: set[str] = field(default_factory=set)

    async def launch(self, job_id: str, env: dict[str, str]) -> str:
        self.launched[job_id] = env
        self.alive.add(f"rec-{job_id}")
        return f"rec-{job_id}"

    async def is_running(self, ref: str) -> bool:
        return ref in self.alive

    async def stop(self, ref: str) -> None:
        self.alive.discard(ref)
        self.stopped.append(ref)


def dispatch(client: TestClient, executor: RecordingExecutor) -> list[str]:
    from z_server.scheduler.queue import dispatch as run

    state = client.app.state.z  # type: ignore[attr-defined]
    return client.portal.call(run, state, executor, "test-worker")  # type: ignore[union-attr,no-any-return]


@dataclass
class ScriptedRunner:
    """Plays the runner side of one job with explicit steps."""

    client: TestClient
    job_id: str
    bootstrap: str
    session: str | None = None
    spec: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def for_job(cls, client: TestClient, executor: RecordingExecutor, job_id: str) -> ScriptedRunner:
        env = executor.launched[job_id]
        assert env["Z_JOB_ID"] == job_id
        return cls(client, job_id, env["Z_JOB_TOKEN"])

    def _headers(self, token: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token or self.session}"}

    def get_spec(self) -> dict[str, Any]:
        resp = self.client.get(f"{INTERNAL}/{self.job_id}/spec", headers=self._headers(self.bootstrap))
        assert resp.status_code == 200, resp.text
        self.spec = resp.json()
        self.session = self.spec["session_token"]
        return self.spec

    def call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self.client.request(method, f"{INTERNAL}/{self.job_id}{path}", headers=self._headers(), **kwargs)

    def events(self, *events: dict[str, Any]) -> dict[str, Any]:
        resp = self.call("POST", "/events", json={"events": list(events)})
        assert resp.status_code == 200, resp.text
        return dict(resp.json())

    def control(self, wait_s: int = 0) -> dict[str, Any]:
        resp = self.call("GET", "/control", params={"wait_s": wait_s})
        assert resp.status_code == 200, resp.text
        return dict(resp.json())

    def checkpoint(self, step: int, blob: bytes) -> httpx.Response:
        return self.call("POST", "/checkpoint", json={"step": step, "blob_b64": base64.b64encode(blob).decode()})

    def workspace(self) -> dict[str, bytes]:
        resp = self.call("GET", "/workspace")
        assert resp.status_code == 200, resp.text
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            return {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}  # type: ignore[union-attr]

    def complete(self, status: str = "succeeded", output: Any = None, **extra: Any) -> httpx.Response:
        body = {"status": status, "steps": extra.pop("steps", 3), "tokens_in": 0, "tokens_out": 0, **extra}
        if output is not None:
            body["output"] = output
        return self.call("POST", "/complete", json=body)
