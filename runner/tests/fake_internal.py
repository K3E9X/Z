"""Fake z-server for runner tests: the `/internal/jobs/...` endpoints of contracts/openapi.yaml implemented with
the z-common models, plus a per-job tool gateway MCP endpoint at `/gateway/mcp`.

Everything received is recorded. Every event is wrapped in the full envelope and validated against
contracts/events.schema.json, every response against openapi.yaml; violations are recorded and fail the test
(`assert_clean()`). Control (pause, cancel, guide messages, answers, key rotation) is scriptable.
"""

from __future__ import annotations

import base64
import hashlib
import io
import itertools
import json
import secrets
import tarfile
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from z_common.contracts import errors as schema_errors
from z_common.contracts import response_errors
from z_common.models.api_internal import (
    ActionIn,
    CheckpointIn,
    EventBatch,
    JobComplete,
    JobSpec,
    MemorySearch,
    QuestionIn,
    SubagentIn,
)

from tests.fakes._mcp import McpEndpoint, McpTool
from tests.fakes._server import FakeServer

REPO = Path(__file__).resolve().parents[2]


def problem(status: int, title: str, kind: str, detail: str | None = None) -> JSONResponse:
    body: dict[str, Any] = {"type": f"urn:z:problem:{kind}", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


@dataclass
class FakeJob:
    spec: dict[str, Any]
    bootstrap_token: str
    session_token: str
    bootstrap_used: bool = False
    archive: bytes | None = None
    manifest: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, Any]] = field(default_factory=list)
    completions: list[dict[str, Any]] = field(default_factory=list)
    control_polls: int = 0
    # control
    state: str = "run"
    reason: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)
    action_states: list[dict[str, Any]] = field(default_factory=list)
    llm_api_key: str | None = None
    memory: list[dict[str, Any]] = field(default_factory=list)
    forbidden_operations: set[str] = field(default_factory=set)
    subagents: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # hooks: called with (job, payload) under the server lock
    on_checkpoint: Callable[[FakeJob, dict[str, Any]], None] | None = None
    on_question: Callable[[FakeJob, dict[str, Any]], None] | None = None
    on_poll: Callable[[FakeJob], None] | None = None
    on_events: Callable[[FakeJob, list[dict[str, Any]]], None] | None = None

    def control(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "state": self.state,
            "reason": self.reason,
            "messages": list(self.messages),
            "answers": list(self.answers),
            "actions": list(self.action_states),
        }
        if self.llm_api_key:
            body["llm_api_key"] = self.llm_api_key
        return body

    def events_of(self, type_: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == type_]

    @property
    def completion(self) -> dict[str, Any] | None:
        return self.completions[-1] if self.completions else None


class FakeZServer(FakeServer):
    def __init__(self) -> None:
        self.jobs: dict[str, FakeJob] = {}
        self.violations: list[str] = []
        self.lock = threading.RLock()
        self._seq = itertools.count(1)
        self.gateway_tools: list[McpTool] = []
        self.gateway_calls: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str, str]] = []
        self.gateway = McpEndpoint("fake-gateway", [], self._gateway_auth)
        super().__init__(self._app())

    # ------------------------------------------------------------------ setup
    def add_job(
        self, spec: JobSpec, *, archive: bytes | None = None, manifest: dict[str, Any] | None = None
    ) -> FakeJob:
        data = spec.model_dump(mode="json", by_alias=True)
        job = FakeJob(spec=data, bootstrap_token="zjb-" + secrets.token_hex(16), session_token=spec.session_token)
        job.archive, job.manifest = archive, manifest
        with self.lock:
            self.jobs[spec.job_id] = job
        return job

    def set_gateway_tools(self, tools: list[McpTool]) -> None:
        self.gateway.tools = {t.name: t for t in tools}

    def assert_clean(self) -> None:
        assert not self.violations, "contract violations:\n" + "\n".join(self.violations)

    # ------------------------------------------------------------------ auth
    def _gateway_auth(self, header: str | None) -> str | None:
        if not header or not header.startswith("Bearer "):
            return None
        token = header.removeprefix("Bearer ")
        with self.lock:
            if any(j.session_token == token for j in self.jobs.values()):
                return "read"
        return None

    def _job(self, job_id: str, request: Request, *, bootstrap: bool = False) -> FakeJob | JSONResponse:
        header = request.headers.get("authorization", "")
        with self.lock:
            job = self.jobs.get(job_id)
            self.requests.append((request.method, request.url.path, "bootstrap" if bootstrap else "session"))
        if job is None:
            return problem(404, "Not found", "not-found")
        token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
        if bootstrap:
            if token != job.bootstrap_token or job.bootstrap_used:
                return problem(401, "Unauthenticated", "unauthenticated", "bootstrap token invalid or already used")
            job.bootstrap_used = True
            return job
        if token != job.session_token:
            return problem(401, "Unauthenticated", "unauthenticated", "session token required")
        return job

    def _check_response(self, operation: str, status: int, body: Any) -> None:
        found = response_errors(operation, status, body)
        if found:
            self.violations.append(f"fake response {operation} {status}: {found[:3]}")

    # ------------------------------------------------------------------ app
    def _app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            async with self.gateway.run():
                yield

        app = FastAPI(lifespan=lifespan)
        app.mount("/gateway/mcp", self.gateway)

        @app.get("/internal/jobs/{job_id}/spec")
        def spec(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request, bootstrap=True)
            if isinstance(job, Response):
                return job
            self._check_response("internalJobSpec", 200, job.spec)
            return JSONResponse(job.spec)

        @app.get("/internal/jobs/{job_id}/workspace")
        def workspace(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            if job.archive is None:
                return problem(404, "Not found", "not-found", "no staged workspace")
            return Response(job.archive, media_type="application/gzip")

        @app.get("/internal/jobs/{job_id}/workspace/manifest")
        def manifest(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            body = job.manifest or {"files": [], "flagged": []}
            self._check_response("internalJobWorkspaceManifest", 200, body)
            return JSONResponse(body)

        @app.post("/internal/jobs/{job_id}/events")
        async def events(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            raw = await request.json()
            try:
                batch = EventBatch.model_validate(raw)
            except ValidationError as exc:
                self.violations.append(f"EventBatch: {exc}")
                return problem(422, "Invalid", "validation", str(exc))
            received = []
            with self.lock:
                for event in raw["events"]:
                    envelope = {
                        "mission_id": job.spec["mission_id"],
                        "job_id": job_id,
                        "seq": next(self._seq),
                        "agent": job.spec["agent"]["name"],
                        **event,
                    }
                    found = schema_errors("events.schema.json", envelope)
                    if found:
                        self.violations.append(f"event {event.get('type')}: {found}")
                    job.events.append(envelope)
                    received.append(envelope)
                if job.on_events is not None:
                    job.on_events(job, received)
                body = job.control()
            assert len(batch.events) == len(received)
            self._check_response("internalPostEvents", 200, body)
            return JSONResponse(body)

        @app.get("/internal/jobs/{job_id}/control")
        def control(job_id: str, request: Request, wait_s: int = 0) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            if not 0 <= wait_s <= 30:
                return problem(422, "Invalid", "validation", "wait_s out of range")
            with self.lock:
                job.control_polls += 1
                if job.on_poll is not None:
                    job.on_poll(job)
                body = job.control()
            if (wait_s and body["state"] == "pause") or (wait_s and not body["answers"]):
                time.sleep(min(wait_s, 0.05))
            self._check_response("internalJobControl", 200, body)
            return JSONResponse(body)

        @app.post("/internal/jobs/{job_id}/artifacts")
        async def artifacts(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            form = await request.form()
            upload = form.get("file")
            kind, name = form.get("kind"), form.get("name")
            if upload is None or isinstance(upload, str) or not isinstance(kind, str) or not isinstance(name, str):
                self.violations.append("artifact upload without kind, name and file")
                return problem(422, "Invalid", "validation")
            content = await upload.read()
            body = {
                "id": f"art-{secrets.token_hex(6)}",
                "job_id": job_id,
                "mission_id": job.spec["mission_id"],
                "kind": kind,
                "name": name,
                "content_type": upload.content_type or "application/octet-stream",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "data_class": "C2",
                "created_at": datetime.now(UTC).isoformat(),
            }
            self._check_response("internalPostArtifact", 201, body)
            with self.lock:
                job.artifacts.append({**body, "content": content})
            return JSONResponse(body, status_code=201)

        @app.post("/internal/jobs/{job_id}/checkpoint")
        async def checkpoint(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            try:
                data = CheckpointIn.model_validate(await request.json())
            except ValidationError as exc:
                self.violations.append(f"CheckpointIn: {exc}")
                return problem(422, "Invalid", "validation")
            with self.lock:
                record = data.model_dump(mode="json")
                job.checkpoints.append(record)
                if job.on_checkpoint is not None:
                    job.on_checkpoint(job, record)
                body = job.control()
            return JSONResponse(body)

        @app.post("/internal/jobs/{job_id}/actions")
        async def actions(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            try:
                action = ActionIn.model_validate(await request.json())
            except ValidationError as exc:
                self.violations.append(f"ActionIn: {exc}")
                return problem(422, "Invalid", "validation")
            if action.operation in job.forbidden_operations or action.operation not in [
                w["operation"] for w in job.spec["writes"]
            ]:
                return problem(403, "Forbidden", "forbidden", f"{action.operation} is not a write of this step")
            target = next(w for w in job.spec["writes"] if w["operation"] == action.operation)
            now = datetime.now(UTC).isoformat()
            body = {
                "id": f"act-{secrets.token_hex(4)}",
                "job_id": job_id,
                "mission_id": job.spec["mission_id"],
                "agent": job.spec["agent"]["name"],
                "connector": action.connector,
                "operation": action.operation,
                "class": target["class"],
                "summary": action.summary,
                "payload": action.payload,
                "preview": {"kind": "json", "data": action.payload},
                "reversible": False,
                "runs_as": {"scope": "service", "identity": "z-bot"},
                "status": "pending",
                "approvals": [],
                "required_approvals": 1,
                "idempotency_key": secrets.token_hex(8),
                "created_at": now,
            }
            self._check_response("internalProposeAction", 201, body)
            with self.lock:
                job.actions.append(body)
            return JSONResponse(body, status_code=201)

        @app.post("/internal/jobs/{job_id}/questions")
        async def questions(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            question = QuestionIn.model_validate(await request.json())
            body = {
                "id": f"q-{secrets.token_hex(4)}",
                "job_id": job_id,
                "mission_id": job.spec["mission_id"],
                "agent": job.spec["agent"]["name"],
                "text": question.text,
                "options": question.options or [],
                "answer": None,
                "answered_by": None,
                "answered_at": None,
                "created_at": datetime.now(UTC).isoformat(),
            }
            self._check_response("internalAskHuman", 201, body)
            with self.lock:
                job.questions.append(body)
                if job.on_question is not None:
                    job.on_question(job, body)
            return JSONResponse(body, status_code=201)

        @app.post("/internal/jobs/{job_id}/memory/search")
        async def memory(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            search = MemorySearch.model_validate(await request.json())
            body = job.memory[: search.top_k or 8]
            self._check_response("internalSearchMemory", 200, body)
            return JSONResponse(body)

        @app.post("/internal/jobs/{job_id}/subagents")
        async def subagents(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            SubagentIn.model_validate(await request.json())
            child = f"job-child-{secrets.token_hex(3)}"
            with self.lock:
                job.subagents[child] = [
                    {"child_job_id": child, "status": "running"},
                    {"child_job_id": child, "status": "succeeded", "output": {"answer": 42}},
                ]
            body = {"child_job_id": child, "status": "queued"}
            self._check_response("internalSpawnSubagent", 201, body)
            return JSONResponse(body, status_code=201)

        @app.get("/internal/jobs/{job_id}/subagents/{child_id}")
        def subagent(job_id: str, child_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            with self.lock:
                states = job.subagents.get(child_id)
                if not states:
                    return problem(404, "Not found", "not-found")
                body = states.pop(0) if len(states) > 1 else states[0]
            return JSONResponse(body)

        @app.post("/internal/jobs/{job_id}/complete")
        async def complete(job_id: str, request: Request) -> Response:
            job = self._job(job_id, request)
            if isinstance(job, Response):
                return job
            raw = await request.json()
            try:
                completion = JobComplete.model_validate(raw)
            except ValidationError as exc:
                self.violations.append(f"JobComplete: {exc}")
                return problem(422, "Invalid", "validation")
            now = datetime.now(UTC).isoformat()
            body = {
                "id": job_id,
                "mission_id": job.spec["mission_id"],
                "step_id": job.spec["step_id"],
                "agent": job.spec["agent"]["name"],
                "agent_version": job.spec["agent"]["version"],
                "input": job.spec["input"],
                "status": completion.status,
                "needs_attention_reason": raw.get("needs_attention_reason"),
                "attempt": job.spec["attempt"],
                "steps_done": completion.steps,
                "model": job.spec["model"],
                "tokens_in": completion.tokens_in,
                "tokens_out": completion.tokens_out,
                "created_at": now,
                "ended_at": now,
                "error": raw.get("error"),
                "output": completion.output,
            }
            self._check_response("internalCompleteJob", 200, body)
            with self.lock:
                job.completions.append(raw)
            return JSONResponse(body)

        return app


# ---------------------------------------------------------------------- builders
def make_archive(files: dict[str, bytes | str], *, extra: Callable[[tarfile.TarFile], None] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        if extra is not None:
            extra(tar)
    return buf.getvalue()


def make_manifest(files: dict[str, bytes | str], flagged: list[str] | None = None) -> dict[str, Any]:
    entries = []
    for name, content in files.items():
        data = content.encode() if isinstance(content, str) else content
        entries.append(
            {
                "path": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "judge": "flagged" if name in (flagged or []) else "clean",
            }
        )
    return {"files": entries, "flagged": list(flagged or [])}


def demo_spec(
    llm_base_url: str,
    server_url: str,
    *,
    job_id: str | None = None,
    **overrides: Any,
) -> JobSpec:
    """A job of the demo pack's summarize-file playbook (packs/demo), overridable per test."""
    import yaml

    pack = REPO / "packs" / "demo"
    agent = yaml.safe_load((pack / "agents" / "summarizer.yaml").read_text())
    skill = (pack / "skills" / "summarize" / "SKILL.md").read_text()
    schema = json.loads((pack / "schemas" / "summary.json").read_text())
    job_id = job_id or f"job-{secrets.token_hex(4)}"
    data: dict[str, Any] = {
        "job_id": job_id,
        "mission_id": "mis-demo",
        "step_id": "summarize",
        "attempt": 1,
        "agent": agent,
        "skills": [{"name": "summarize", "version": "1.0.0", "content": skill}],
        "memory": [{"id": "mem-1", "scope": "org", "text": "Release notes live in notes.md."}],
        "prompt": "Apply the summarize skill to the file notes.md.",
        "input": {"file": "notes.md"},
        "output": {"schema": schema, "max_repair_attempts": 2},
        "writes": [],
        "workspace": {"mode": "files", "archive_url": f"{server_url}/internal/jobs/{job_id}/workspace"},
        "tools": {"local": ["read"], "builtins": [], "gateway": []},
        "limits": agent["limits"],
        "model": "z-fast",
        "escalate_model": "z-heavy",
        "llm": {"base_url": llm_base_url, "api_key": "sk-mission-" + secrets.token_hex(12)},
        "gateway_url": f"{server_url}/gateway/mcp?job={job_id}",
        "server_url": server_url,
        "session_token": "zjs-" + secrets.token_hex(20),
        "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }
    data.update(overrides)
    return JobSpec.model_validate(data)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def tool_call(name: str, arguments: dict[str, Any] | str, text: str | None = None) -> dict[str, Any]:
    """A fake LLM step calling one tool (a string is sent verbatim: malformed JSON)."""
    step: dict[str, Any] = {"tool_calls": [{"name": name, "arguments": arguments}]}
    if text:
        step["text"] = text
    return step


def finish(message: str = "done") -> dict[str, Any]:
    return tool_call("finish", {"message": message})


def write_result(result: dict[str, Any] | str) -> dict[str, Any]:
    content = result if isinstance(result, str) else json.dumps(result)
    return tool_call("edit", {"command": "write", "path": "result.json", "content": content})


GOOD_SUMMARY = {
    "title": "Release 2.4 adds single sign-on",
    "bullets": [
        "Version 2.4 adds single sign-on with the corporate identity provider.",
        "Password login is removed for staff accounts.",
        "Two medium vulnerabilities in the export module are fixed.",
    ],
    "source": "notes.md",
}
