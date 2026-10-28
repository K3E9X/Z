"""The job loop (docs/04 §1, §4, §5, §7): bootstrap, stage the workspace, run the agent step by step under the
platform guardrails, checkpoint after every step, validate and repair the output, upload artifacts, complete.
"""

from __future__ import annotations

import base64
import binascii
import logging
import posixpath
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from z_common.models.api_internal import CheckpointIn, JobComplete, JobError
from z_common.models.enums import ArtifactKind, NeedsAttentionReason, WorkspaceMode

from z_runner.artifacts import ArtifactStore
from z_runner.backend.base import AgentBackend, StepResult, SupportsModelSwitch
from z_runner.checkpoint import Checkpoint, CheckpointError
from z_runner.client import ApiError, InternalClient
from z_runner.control import ControlHub
from z_runner.events import EventBuffer
from z_runner.guardrails import LoopDetector
from z_runner.output import OutputSchemaError, check_output
from z_runner.prompt import guide_message, repair_message, task_message
from z_runner.redaction import Redactor
from z_runner.tools import OUTPUT_FILE, build_toolset
from z_runner.tools.base import ToolContext
from z_runner.tools.gateway import GatewayClient
from z_runner.tools.shell import sandbox_env
from z_runner.workspace import (
    PRIVATE_DIR,
    ArchiveError,
    ArchiveLimits,
    StagedInputs,
    Workspace,
    check_manifest,
    extract_tar,
)

log = logging.getLogger("z_runner")

# Exit codes (documented in runner/README.md).
EXIT_CODES = {
    "succeeded": 0,
    "failed": 1,
    "needs_attention": 3,
    "budget_exceeded": 4,
    "timed_out": 5,
    "cancelled": 6,
}
EXIT_USAGE = 2
EXIT_BOOTSTRAP = 10
EXIT_COMPLETE_FAILED = 11
EXIT_INTERRUPTED = 12

REPORT_FILE = "report.md"
PATCH_NAME = "changes.patch"
MAX_PATCH = 20 * 1024 * 1024


@dataclass
class RunnerConfig:
    workspace_dir: Path = Path("/workspace")
    state_dir: Path | None = None
    loop_repeat_limit: int = 3
    no_progress_limit: int = 8
    escalate_after_failures: int = 3
    archive_limits: ArchiveLimits = field(default_factory=ArchiveLimits)
    max_archive_bytes: int = 1024**3
    strict_events: bool = False
    http_transport: httpx.BaseTransport | None = None


Status = Literal["succeeded", "failed", "needs_attention", "budget_exceeded", "timed_out", "cancelled"]


@dataclass
class Outcome:
    status: Status
    reason: NeedsAttentionReason | None = None
    output: dict[str, Any] | None = None
    error: JobError | None = None


@dataclass
class RunnerState:
    steps: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    repairs: int = 0
    failure_streak: int = 0
    escalated: bool = False
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunnerState:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


BackendFactory = Callable[[Path], AgentBackend]


def _default_backend(persistence_root: Path) -> AgentBackend:
    from z_runner.backend.openhands import OpenHandsBackend  # imports the SDK lazily

    return OpenHandsBackend(persistence_root)


class Runner:
    def __init__(
        self,
        server_url: str,
        job_id: str,
        bootstrap_token: str,
        config: RunnerConfig | None = None,
        backend_factory: BackendFactory = _default_backend,
        redactor: Redactor | None = None,
    ) -> None:
        self.server_url = server_url
        self.job_id = job_id
        self._bootstrap_token = bootstrap_token
        self.config = config or RunnerConfig()
        self._backend_factory = backend_factory
        self.redactor = redactor or Redactor()
        self.redactor.add_secret(bootstrap_token)
        self._terminate = False
        self._current_llm_key: str | None = None
        self.state = RunnerState()
        self.loop = LoopDetector(self.config.loop_repeat_limit, self.config.no_progress_limit)

    def request_termination(self) -> None:
        """SIGTERM: stop after the current step and exit without completing (the checkpoint allows a resume)."""
        self._terminate = True

    # ------------------------------------------------------------------ entry
    def run(self) -> int:
        client = InternalClient(
            self.server_url, self.job_id, self._bootstrap_token, transport=self.config.http_transport
        )
        try:
            spec = client.get_spec()
        except ApiError as exc:
            log.error("could not fetch the job spec: %s", exc)
            client.close()
            return EXIT_BOOTSTRAP
        if spec.job_id != self.job_id:
            log.error("the job spec is for another job")
            client.close()
            return EXIT_BOOTSTRAP
        client.use_session_token(spec.session_token)
        self.redactor.add_secret(spec.session_token)
        self.redactor.add_secret(spec.llm.api_key)
        self._bootstrap_token = ""
        self.spec = spec
        self.client = client
        self.sink = EventBuffer(client.post_events, self.redactor, spec.step_id, strict=self.config.strict_events)
        self.control = ControlHub(client.get_control, self.sink.flush)
        self.artifacts = ArtifactStore(client, self.sink, self.redactor)
        self.state.model = spec.model
        backend: AgentBackend | None = None
        try:
            try:
                outcome, backend = self._run_job()
            except _Interrupted:
                log.warning("terminated: exiting after the checkpoint, without completing the job")
                self._safe_flush()
                return EXIT_INTERRUPTED
            except OutputSchemaError as exc:
                outcome = Outcome("failed", error=JobError(class_="invalid_output_schema", message=str(exc)))
            except (ArchiveError, CheckpointError) as exc:
                outcome = Outcome("failed", error=JobError(class_="workspace", message=self.redactor.text(str(exc))))
            except ApiError as exc:
                outcome = Outcome("failed", error=JobError(class_="platform", message=self.redactor.text(str(exc))))
            except Exception as exc:
                log.exception("job failed")
                outcome = Outcome(
                    "failed", error=JobError(class_=type(exc).__name__, message=self.redactor.text(str(exc))[:2000])
                )
            if backend is not None:
                try:
                    backend.stop(outcome.status)
                except Exception:
                    log.exception("backend stop failed")
            return self._complete(outcome)
        finally:
            client.close()

    # ------------------------------------------------------------------ job
    def _run_job(self) -> tuple[Outcome, AgentBackend | None]:
        spec = self.spec
        workspace = Workspace(self.config.workspace_dir)
        staged = self._stage(workspace)
        self._materialize_skills(workspace)
        baseline = workspace.snapshot()
        checkpoint = self._load_checkpoint()
        if checkpoint is not None:
            workspace.apply_delta(checkpoint.workspace, self.config.archive_limits)
        self.workspace, self.baseline = workspace, baseline
        context = ToolContext(
            spec=spec,
            workspace=workspace,
            sink=self.sink,
            client=self.client,
            redactor=self.redactor,
            control=self.control,
            artifacts=self.artifacts,
            staged=staged,
        )
        tools = build_toolset(spec, GatewayClient(spec.gateway_url, lambda: spec.session_token))
        tools.context = context
        self.context = context
        state_dir = self.config.state_dir or Path(tempfile.mkdtemp(prefix="z-runner-state-"))
        backend = self._backend_factory(state_dir / "conversation")
        if checkpoint is not None:
            backend.restore(checkpoint.backend)
        backend.start(spec, self.sink, tools)
        if checkpoint is None:
            backend.inject_message(self.redactor.text(task_message(spec, staged, OUTPUT_FILE)))
        else:
            self.sink.emit(
                "message", {"role": "system", "text": f"Resumed from the checkpoint of step {self.state.steps}."}
            )
        if self.state.model != spec.model and isinstance(backend, SupportsModelSwitch):
            backend.set_llm(self.state.model, spec.llm.api_key)
        self.control.poll(0)
        return self._loop(backend), backend

    def _loop(self, backend: AgentBackend) -> Outcome:
        while True:
            if self.control.cancelled:
                return Outcome("cancelled")
            if self.control.paused:
                parked = self._park()
                if parked is not None:
                    return parked
            self._apply_control(backend)
            limit = self._limits_reached()
            if limit is not None:
                return limit
            self.context.progress.clear()
            result = backend.step()
            self.state.steps += 1
            self.state.tokens_in += result.tokens_in
            self.state.tokens_out += result.tokens_out
            outcome = self._after_step(backend, result)
            self._checkpoint(backend)
            if outcome is not None:
                return outcome
            if self._terminate:
                raise _Interrupted()

    def _after_step(self, backend: AgentBackend, result: StepResult) -> Outcome | None:
        spec = self.spec
        for name, args in result.tool_calls:
            self.loop.record_call(name, args)
        if result.progress or self.context.progress:
            self.loop.record_progress()
        loop_reason = self.loop.end_step()
        failed = result.tool_failures + result.validation_errors
        if failed and not result.tool_successes:
            self.state.failure_streak += 1
        elif result.tool_successes:
            self.state.failure_streak = 0
        if result.status == "error":
            return Outcome(
                "failed",
                error=JobError(class_="agent", message=self.redactor.text(result.error or "agent error")[:2000]),
            )
        if result.status == "confirmation_required":
            return Outcome("needs_attention", NeedsAttentionReason.guardrail_confirm)
        if result.status == "stuck" or loop_reason:
            detail = loop_reason or "the harness detected a repeating pattern"
            self.sink.emit("message", {"role": "system", "text": f"Loop detected: {detail}."})
            return Outcome("needs_attention", NeedsAttentionReason.loop_detected)
        if result.status == "finished":
            if spec.output is None:
                return Outcome("succeeded")
            check = check_output(self.workspace, OUTPUT_FILE, spec.output.schema_, result.final_message)
            if check.ok:
                return Outcome("succeeded", output=check.output)
            self.sink.emit(
                "validation_error",
                {
                    "where": "output",
                    "errors": [e.model_dump(mode="json") for e in check.errors],
                    "attempt": self.state.repairs + 1,
                },
            )
            if self.state.repairs >= spec.output.max_repair_attempts:
                return Outcome("needs_attention", NeedsAttentionReason.invalid_output)
            self.state.repairs += 1
            self.state.failure_streak += 1
            messages = [f"{e.path}: {e.message}" for e in check.errors]
            backend.inject_message(
                repair_message(OUTPUT_FILE, messages, self.state.repairs, spec.output.max_repair_attempts)
            )
        self._maybe_escalate(backend)
        return None

    def _maybe_escalate(self, backend: AgentBackend) -> None:
        spec = self.spec
        if (
            spec.escalate_model
            and not self.state.escalated
            and self.state.failure_streak >= self.config.escalate_after_failures
            and isinstance(backend, SupportsModelSwitch)
        ):
            previous = self.state.model
            backend.set_llm(spec.escalate_model, self._llm_key())
            self.state.model = spec.escalate_model
            self.state.escalated = True
            self.state.failure_streak = 0
            self.sink.emit(
                "message",
                {
                    "role": "system",
                    "text": f"Escalated from {previous} to {spec.escalate_model} after repeated failures.",
                },
            )

    def _llm_key(self) -> str:
        return self._current_llm_key or self.spec.llm.api_key

    def _limits_reached(self) -> Outcome | None:
        spec = self.spec
        if datetime.now(UTC) >= spec.deadline:
            return Outcome("timed_out", error=JobError(class_="deadline", message="the job deadline passed"))
        if self.state.steps >= spec.limits.max_steps:
            return Outcome(
                "budget_exceeded", error=JobError(class_="steps", message=f"step limit {spec.limits.max_steps} reached")
            )
        if self.state.tokens_in + self.state.tokens_out >= spec.limits.max_tokens:
            return Outcome("budget_exceeded", error=JobError(class_="budget", message="token budget exhausted"))
        return None

    # ------------------------------------------------------------------ control
    def _apply_control(self, backend: AgentBackend) -> None:
        key = self.control.take_llm_key()
        if key:
            self.redactor.add_secret(key)
            self._current_llm_key = key
            if isinstance(backend, SupportsModelSwitch):
                backend.set_llm(self.state.model, key)
        for message in self.control.take_messages():
            backend.inject_message(self.redactor.text(guide_message(message.text, message.by)))
        for answer in self.control.take_unclaimed_answers():
            backend.inject_message(
                self.redactor.text(
                    f"[Answer to your question {answer.question_id} from {answer.answered_by}] {answer.answer}"
                )
            )
        updates = self.control.take_action_updates()
        notable = [(a, s) for a, s in updates if s != "pending"]
        if notable:
            listed = ", ".join(f"{a} is {s}" for a, s in notable)
            backend.inject_message(f"[Platform] Status of your proposed actions: {listed}.")

    def _park(self) -> Outcome | None:
        """Pause: stay after the checkpoint of the last step until resumed, cancelled or out of time."""
        self.sink.emit("status", {"entity": "job", "from": "running", "to": "paused", "reason": self.control.reason})
        self._safe_flush()
        while self.control.paused:
            remaining = (self.spec.deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return Outcome(
                    "timed_out", error=JobError(class_="deadline", message="the deadline passed while paused")
                )
            if self._terminate:
                raise _Interrupted()
            self.control.poll(int(max(1, min(30, remaining))))
        if self.control.cancelled:
            return Outcome("cancelled")
        self.sink.emit("status", {"entity": "job", "from": "paused", "to": "running", "reason": None})
        return None

    def _safe_flush(self) -> None:
        try:
            self.control.ingest(self.sink.flush())
        except ApiError as exc:
            log.warning("could not post events: %s", exc)

    # ------------------------------------------------------------------ workspace
    def _stage(self, workspace: Workspace) -> StagedInputs:
        spec = self.spec
        if spec.workspace.mode == WorkspaceMode.none or not spec.workspace.archive_url:
            return StagedInputs(files=[], flagged=[], blocked=[])
        with tempfile.NamedTemporaryFile(prefix="z-workspace-", suffix=".tar.gz") as archive:
            self.client.download_workspace(Path(archive.name), self.config.max_archive_bytes)
            with open(archive.name, "rb") as fh:
                extract_tar(fh, workspace.root, self.config.archive_limits)
        manifest = self.client.get_manifest()
        return check_manifest(workspace, manifest)

    def _materialize_skills(self, workspace: Workspace) -> None:
        for skill in self.spec.skills:
            for resource in skill.resources or []:
                rel = posixpath.normpath(resource.path)
                if rel.startswith(("/", "..")) or "/../" in f"/{rel}/":
                    raise ArchiveError(f"unsafe skill resource path {resource.path!r}")
                target = workspace.resolve(f"{PRIVATE_DIR}/skills/{skill.name}/{rel}", for_write=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    target.write_bytes(base64.b64decode(resource.content_b64, validate=True))
                except (binascii.Error, ValueError) as exc:
                    raise ArchiveError(f"skill resource {resource.path!r} is not base64") from exc

    # ------------------------------------------------------------------ checkpoints
    def _load_checkpoint(self) -> Checkpoint | None:
        if not self.spec.checkpoint_b64:
            return None
        checkpoint = Checkpoint.decode(self.spec.checkpoint_b64)
        runner = checkpoint.runner
        self.state = RunnerState.from_dict(runner.get("state", {}))
        self.loop.load_state(runner.get("loop", {}))
        self.control.load_state(runner.get("control", {}))
        if self.spec.resume_from_step is not None and self.spec.resume_from_step != self.state.steps:
            log.warning(
                "resume_from_step %s differs from the checkpoint step %s", self.spec.resume_from_step, self.state.steps
            )
        return checkpoint

    def _checkpoint(self, backend: AgentBackend) -> None:
        delta, _ = self.workspace.delta(self.baseline)
        blob = Checkpoint(
            backend=backend.checkpoint(),
            runner={"state": self.state.to_dict(), "loop": self.loop.to_state(), "control": self.control.to_state()},
            workspace=delta,
        ).encode()
        self.control.ingest(
            self.client.post_checkpoint(
                CheckpointIn(
                    step=self.state.steps,
                    blob_b64=blob,
                    tokens_in=self.state.tokens_in,
                    tokens_out=self.state.tokens_out,
                )
            )
        )
        self.sink.emit("checkpoint", {"step": self.state.steps, "size": len(blob)})
        self._safe_flush()

    # ------------------------------------------------------------------ completion
    def _upload_outputs(self, outcome: Outcome) -> str | None:
        workspace = getattr(self, "workspace", None)
        if workspace is None:
            return None
        result_path = workspace.root / OUTPUT_FILE
        if result_path.is_file() and not result_path.is_symlink():
            self.artifacts.try_upload(ArtifactKind.result, OUTPUT_FILE, result_path.read_bytes(), "application/json")
        report_id = None
        report_path = workspace.root / REPORT_FILE
        if report_path.is_file() and not report_path.is_symlink():
            report_id = self.artifacts.try_upload(
                ArtifactKind.report, REPORT_FILE, report_path.read_bytes(), "text/markdown"
            )
        if self.spec.workspace.mode == WorkspaceMode.git_clone and (workspace.root / ".git").is_dir():
            patch = self._git_patch(workspace)
            if patch:
                self.artifacts.try_upload(ArtifactKind.patch, PATCH_NAME, patch, "text/x-diff")
        return report_id

    @staticmethod
    def _git_patch(workspace: Workspace) -> bytes | None:
        excludes = [
            f":(exclude){PRIVATE_DIR}",
            f":(exclude){OUTPUT_FILE}",
            f":(exclude){REPORT_FILE}",
            ":(exclude)documents",
        ]
        vcs = shutil.which("git")
        if vcs is None:
            return None
        env = sandbox_env()
        try:
            subprocess.run(  # noqa: S603
                [vcs, "-C", str(workspace.root), "add", "--intent-to-add", "--", ".", *excludes],
                env=env,
                check=True,
                capture_output=True,
                timeout=120,
            )
            diff = subprocess.run(  # noqa: S603
                [
                    vcs,
                    "-C",
                    str(workspace.root),
                    "-c",
                    "core.quotepath=off",
                    "diff",
                    "--binary",
                    "HEAD",
                    "--",
                    ".",
                    *excludes,
                ],
                env=env,
                check=True,
                capture_output=True,
                timeout=120,
            ).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("could not compute the patch: %s", exc)
            return None
        if not diff.strip() or len(diff) > MAX_PATCH:
            return None
        return diff

    def _complete(self, outcome: Outcome) -> int:
        report_id: str | None = None
        try:
            report_id = self._upload_outputs(outcome)
        except Exception:
            log.exception("artifact upload failed")
        completion = JobComplete(
            status=outcome.status,
            output=outcome.output,
            report_artifact=report_id,
            needs_attention_reason=outcome.reason,
            error=outcome.error,
            tokens_in=self.state.tokens_in,
            tokens_out=self.state.tokens_out,
            steps=self.state.steps,
        )
        self._safe_flush()
        try:
            self.client.complete(completion)
        except ApiError as exc:
            log.error("could not complete the job: %s", exc)
            return EXIT_COMPLETE_FAILED
        log.info("job %s: %s after %d steps", self.job_id, outcome.status, self.state.steps)
        return EXIT_CODES[outcome.status]


class _Interrupted(Exception):
    """Raised to leave the loop on SIGTERM after the step's checkpoint."""
