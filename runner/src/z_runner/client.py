"""Typed client of the z-server internal API (contracts/openapi.yaml, `/internal/jobs/...`).

* Requests and responses use the z-common models; responses that z-common does not model (`Artifact`,
  `ProposedAction`, `Question`, `Job`) are read through small views of the contract fields the runner needs.
* Retries with exponential backoff: idempotent calls retry on transport errors and on 429/502/503/504;
  non-idempotent calls retry only when the request provably never reached the server (connection refused,
  connect timeout).
* Tokens are never logged: logs carry the method, the path template and the status only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from z_common.models.api_internal import (
    ActionIn,
    CheckpointIn,
    EventBatch,
    JobComplete,
    JobControl,
    JobSpec,
    MemorySearch,
    MemorySnippet,
    QuestionIn,
    SubagentIn,
    SubagentStatus,
    WorkspaceManifest,
)
from z_common.models.base import ZModel
from z_common.models.common import Problem

log = logging.getLogger("z_runner.client")

T = TypeVar("T", bound=BaseModel)

RETRY_STATUSES = frozenset({429, 502, 503, 504})
_SAFE_TRANSPORT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)
_TRANSPORT_ERRORS = (httpx.TransportError,)


class ApiError(Exception):
    """An internal API call failed. `status` is None for transport errors."""

    def __init__(self, operation: str, status: int | None, problem: Problem | None = None, detail: str = "") -> None:
        self.operation = operation
        self.status = status
        self.problem = problem
        message = problem.detail or problem.title if problem else detail
        super().__init__(f"{operation}: {status if status is not None else 'transport error'} {message}".strip())

    @property
    def problem_type(self) -> str | None:
        return self.problem.type if self.problem else None


class _View(BaseModel):
    """Read-only view of a contract response: only the fields the runner uses, the rest is ignored."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ArtifactView(_View):
    id: str
    kind: str
    name: str
    size: int
    sha256: str


class ProposedActionView(_View):
    id: str
    operation: str
    status: str


class QuestionView(_View):
    id: str
    text: str
    options: list[str] = []


class JobView(_View):
    id: str
    status: str


_SNIPPETS = TypeAdapter(list[MemorySnippet])


class InternalClient:
    """One job's view of the internal API. Starts with the bootstrap token; `use_session_token` switches."""

    def __init__(
        self,
        server_url: str,
        job_id: str,
        token: str,
        *,
        timeout: float = 30.0,
        max_attempts: int = 5,
        backoff_s: float = 0.5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.job_id = job_id
        self._token = token
        self._max_attempts = max_attempts
        self._backoff_s = backoff_s
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=server_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=False,  # never forward the token to another origin
            transport=transport,
            headers={"User-Agent": "z-runner"},
        )

    # ------------------------------------------------------------------ plumbing
    def use_session_token(self, token: str) -> None:
        self._token = token

    def close(self) -> None:
        self._http.close()

    def _path(self, suffix: str) -> str:
        return f"/internal/jobs/{self.job_id}/{suffix}"

    def _send(
        self,
        operation: str,
        method: str,
        suffix: str,
        *,
        idempotent: bool,
        expected: tuple[int, ...] = (200,),
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            headers = {"Authorization": f"Bearer {self._token}", **kwargs.pop("headers", {})}
            try:
                extra: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
                response = self._http.request(method, self._path(suffix), headers=headers, **kwargs, **extra)
            except _TRANSPORT_ERRORS as exc:
                retryable = idempotent or isinstance(exc, _SAFE_TRANSPORT_ERRORS)
                log.warning("%s %s: %s (attempt %d)", method, operation, type(exc).__name__, attempt)
                if retryable and attempt < self._max_attempts:
                    self._sleep(self._backoff_s * 2 ** (attempt - 1))
                    continue
                raise ApiError(operation, None, detail=type(exc).__name__) from exc
            if response.status_code in expected:
                log.debug("%s %s -> %d", method, operation, response.status_code)
                return response
            if idempotent and response.status_code in RETRY_STATUSES and attempt < self._max_attempts:
                log.warning("%s %s -> %d, retrying (attempt %d)", method, operation, response.status_code, attempt)
                self._sleep(self._retry_after(response) or self._backoff_s * 2 ** (attempt - 1))
                continue
            log.warning("%s %s -> %d", method, operation, response.status_code)
            raise ApiError(operation, response.status_code, self._problem(response), detail=response.reason_phrase)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value and value.isdigit():
            return min(float(value), 30.0)
        return None

    @staticmethod
    def _problem(response: httpx.Response) -> Problem | None:
        if not response.headers.get("content-type", "").startswith(("application/problem+json", "application/json")):
            return None
        try:
            return Problem.model_validate(response.json())
        except (ValueError, ValidationError):
            return None

    @staticmethod
    def _parse(operation: str, model: type[T], response: httpx.Response) -> T:
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ApiError(
                operation, response.status_code, detail=f"response does not match the contract: {exc}"
            ) from exc

    @staticmethod
    def _body(model: ZModel) -> dict[str, Any]:
        # ZModel's serializer drops unset optional fields and keeps required nullable ones (contract shapes).
        return model.model_dump(mode="json", by_alias=True)

    # ------------------------------------------------------------------ endpoints
    def get_spec(self) -> JobSpec:
        """Consumes the single-use bootstrap token: only a request that never reached the server is retried."""
        response = self._send("internalJobSpec", "GET", "spec", idempotent=False)
        return self._parse("internalJobSpec", JobSpec, response)

    def download_workspace(self, destination: Path, max_bytes: int) -> int:
        """Stream the staged workspace archive (tar.gz) to `destination`; refuse archives over `max_bytes`."""
        operation = "internalJobWorkspace"
        attempt = 0
        while True:
            attempt += 1
            try:
                with self._http.stream(
                    "GET", self._path("workspace"), headers={"Authorization": f"Bearer {self._token}"}
                ) as response:
                    if response.status_code != 200:
                        response.read()
                        if response.status_code in RETRY_STATUSES and attempt < self._max_attempts:
                            self._sleep(self._backoff_s * 2 ** (attempt - 1))
                            continue
                        raise ApiError(operation, response.status_code, self._problem(response))
                    size = 0
                    with destination.open("wb") as fh:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise ApiError(
                                    operation, 200, detail=f"workspace archive larger than {max_bytes} bytes"
                                )
                            fh.write(chunk)
                    return size
            except _TRANSPORT_ERRORS as exc:
                if attempt < self._max_attempts:
                    self._sleep(self._backoff_s * 2 ** (attempt - 1))
                    continue
                raise ApiError(operation, None, detail=type(exc).__name__) from exc

    def get_manifest(self) -> WorkspaceManifest:
        response = self._send("internalJobWorkspaceManifest", "GET", "workspace/manifest", idempotent=True)
        return self._parse("internalJobWorkspaceManifest", WorkspaceManifest, response)

    def post_events(self, batch: EventBatch) -> JobControl:
        response = self._send("internalPostEvents", "POST", "events", idempotent=False, json=self._body(batch))
        return self._parse("internalPostEvents", JobControl, response)

    def get_control(self, wait_s: int = 0) -> JobControl:
        wait_s = max(0, min(30, wait_s))
        response = self._send(
            "internalJobControl",
            "GET",
            "control",
            idempotent=True,
            params={"wait_s": wait_s},
            timeout=wait_s + 30.0,
        )
        return self._parse("internalJobControl", JobControl, response)

    def post_artifact(self, kind: str, name: str, content: bytes, content_type: str) -> ArtifactView:
        """Multipart upload: form fields `kind`, `name` (z-common `ArtifactMeta`) and the `file` part."""
        response = self._send(
            "internalPostArtifact",
            "POST",
            "artifacts",
            idempotent=False,
            expected=(200, 201),
            data={"kind": kind, "name": name},
            files={"file": (name, content, content_type)},
        )
        return self._parse("internalPostArtifact", ArtifactView, response)

    def post_checkpoint(self, checkpoint: CheckpointIn) -> JobControl:
        """Idempotent per step: the server keeps the latest checkpoint."""
        response = self._send("internalCheckpoint", "POST", "checkpoint", idempotent=True, json=self._body(checkpoint))
        return self._parse("internalCheckpoint", JobControl, response)

    def propose_action(self, action: ActionIn) -> ProposedActionView:
        response = self._send(
            "internalProposeAction", "POST", "actions", idempotent=False, expected=(200, 201), json=self._body(action)
        )
        return self._parse("internalProposeAction", ProposedActionView, response)

    def ask_question(self, question: QuestionIn) -> QuestionView:
        response = self._send(
            "internalAskHuman", "POST", "questions", idempotent=False, expected=(200, 201), json=self._body(question)
        )
        return self._parse("internalAskHuman", QuestionView, response)

    def search_memory(self, search: MemorySearch) -> list[MemorySnippet]:
        response = self._send("internalSearchMemory", "POST", "memory/search", idempotent=True, json=self._body(search))
        try:
            return _SNIPPETS.validate_python(response.json())
        except (ValueError, ValidationError) as exc:
            raise ApiError("internalSearchMemory", response.status_code, detail=str(exc)) from exc

    def spawn_subagent(self, subagent: SubagentIn) -> SubagentStatus:
        response = self._send(
            "internalSpawnSubagent",
            "POST",
            "subagents",
            idempotent=False,
            expected=(200, 201),
            json=self._body(subagent),
        )
        return self._parse("internalSpawnSubagent", SubagentStatus, response)

    def get_subagent(self, child_id: str) -> SubagentStatus:
        response = self._send("internalSubagentStatus", "GET", f"subagents/{child_id}", idempotent=True)
        return self._parse("internalSubagentStatus", SubagentStatus, response)

    def complete(self, completion: JobComplete) -> JobView:
        """Completing twice with the same body is harmless; transport failures are retried."""
        response = self._send("internalCompleteJob", "POST", "complete", idempotent=True, json=self._body(completion))
        return self._parse("internalCompleteJob", JobView, response)
