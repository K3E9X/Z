"""`OpenHandsBackend`: the OpenHands Software Agent SDK (1.56) behind `AgentBackend` (docs/04 §1, ADR 0001).

How the SDK is used (verified against the installed sources):

* `LLM(model="litellm_proxy/<alias>", base_url=spec.llm.base_url, api_key=spec.llm.api_key)`: LiteLLM's
  `litellm_proxy` provider sends the alias unchanged to the Z LiteLLM gateway (invariant 1: no provider URL).
* `Agent(system_prompt=..., tools=[Tool(name=<registry key>)], include_default_tools=["FinishTool", "ThinkTool"])`
  with the Z system prompt inline (no OpenHands persona, no agent context: no implicit skills, memory or date,
  so the prompt prefix is stable). Each Z tool is a `ZSdkTool` instance registered under `z:<job id>:<tool>`.
* `LocalConversation` with `persistence_dir` (base_state.json + events/), `max_iteration_per_run=1` so that
  `run()` executes exactly one agent step, stuck detection on, the SDK security analyzer (LLM risk + pattern +
  policy rails) with `ConfirmRisky(HIGH)`; a confirmation request ends the job in `needs_attention`
  (`guardrail_confirm`). `send_message()` injects the task, guidance and repair messages.
* The conversation never loads ambient content from the (untrusted) workspace: no plugins, hooks, project
  skills, `AGENTS.md` or file-based agents (`_ZConversation`).
* Checkpoint = tar.gz of the conversation persistence directory; restore = extract it before the conversation
  is created with the same deterministic conversation id, which makes the SDK resume from it.
"""

from __future__ import annotations

import io
import json
import logging
import re
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from openhands.sdk import LLM, Agent, LocalConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm.exceptions import (
    LLMAuthenticationError,
    LLMContextWindowExceedError,
    LLMNoResponseError,
    LLMRateLimitError,
    LLMServiceUnavailableError,
    LLMTimeoutError,
)
from openhands.sdk.security import (
    ConfirmRisky,
    EnsembleSecurityAnalyzer,
    LLMSecurityAnalyzer,
    PatternSecurityAnalyzer,
    PolicyRailSecurityAnalyzer,
    SecurityRisk,
)
from openhands.sdk.tool import Tool, register_tool
from pydantic import SecretStr
from z_common.models.api_internal import JobSpec

from z_runner.backend.base import StepResult
from z_runner.backend.openhands.sdk_tools import ValidationRecorder, ZSdkTool
from z_runner.backend.openhands.translate import EventTranslator
from z_runner.events import EventSink
from z_runner.prompt import system_prompt
from z_runner.redaction import Redactor
from z_runner.tools.base import ToolContext, ToolSet
from z_runner.workspace import ArchiveLimits, extract_tar

log = logging.getLogger("z_runner.backend.openhands")

LITELLM_PROXY_PREFIX = "litellm_proxy/"
USAGE_ID = "agent"
DEFAULT_TOOLS = ["FinishTool", "ThinkTool"]


def litellm_model(alias: str) -> str:
    """LiteLLM model string for a Z model alias behind the LiteLLM proxy."""
    return LITELLM_PROXY_PREFIX + alias


def alias_of(model: str) -> str:
    return model.removeprefix(LITELLM_PROXY_PREFIX)


_BUDGET = re.compile(r"(?i)budget.{0,40}(exceed|reached|spent)|exceeded.{0,20}budget")


def classify_llm_error(exc: BaseException) -> str:
    """Error class of a failed step (StepResult.error_class)."""
    if _BUDGET.search(str(exc)):
        return "llm_budget"  # LiteLLM refuses calls once the mission's virtual key budget is spent
    if isinstance(exc, LLMRateLimitError | LLMTimeoutError | LLMServiceUnavailableError | LLMNoResponseError):
        return "llm_unavailable"
    if isinstance(exc, LLMAuthenticationError):
        return "llm_auth"
    if isinstance(exc, LLMContextWindowExceedError):
        return "context_window"
    return "agent"


class _OneLineFilter(logging.Filter):
    """The SDK logs an ERROR when `max_iteration_per_run=1` stops `run()`: that is how Z steps, not an error."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "maximum iterations limit" not in record.getMessage()


logging.getLogger("openhands.sdk.conversation.impl.local_conversation").addFilter(_OneLineFilter())


class _ZConversation(LocalConversation):
    """A LocalConversation that never loads ambient content: the workspace is untrusted (invariant 6).

    LocalConversation._ensure_plugins_loaded() would load plugins (hooks that run commands, MCP servers,
    skills) from `<workspace>/.agents/plugins` and `<workspace>/.openhands/plugins`, and
    _register_file_based_agents() would register agents from `<workspace>/.openhands/agents`.
    """

    def _ensure_plugins_loaded(self) -> None:
        self._plugins_loaded = True

    def _register_file_based_agents(self) -> None:
        return None


class OpenHandsBackend:
    def __init__(self, persistence_root: Path | None = None) -> None:
        self._persistence_root = persistence_root
        self._restore_blob: bytes | None = None
        self._conversation: LocalConversation | None = None
        self._translator: EventTranslator | None = None
        self._spec: JobSpec | None = None
        self._tool_keys: list[str] = []
        self._seen_responses: set[str] = set()
        self._sink: EventSink | None = None
        self._model: str = ""
        self._api_key: str = ""
        self._redactor: Redactor | None = None
        self.resumed = False

    # ------------------------------------------------------------------ AgentBackend
    def restore(self, blob: bytes) -> None:
        if self._conversation is not None:
            raise RuntimeError("restore() must be called before start()")
        self._restore_blob = blob

    def start(self, spec: JobSpec, sink: EventSink, tools: ToolSet) -> None:
        if tools.context is None:
            raise ValueError("the toolset has no ToolContext")
        self._spec = spec
        self._sink = sink
        self._redactor = tools.context.redactor
        root = self._persistence_root or Path(tempfile.mkdtemp(prefix="z-conversation-"))
        root.mkdir(parents=True, exist_ok=True)
        self._persistence_root = root
        if self._restore_blob is not None:
            extract_tar(io.BytesIO(self._restore_blob), root, ArchiveLimits())
            self.resumed = True
            self._restore_blob = None
        recorder = ValidationRecorder()
        self._tool_keys = self._register(spec, tools, tools.context, recorder)
        self._translator = EventTranslator(sink, tools, recorder)
        self._model = spec.model
        self._api_key = spec.llm.api_key
        agent = Agent(
            llm=self._llm(spec.model, spec.llm.api_key),
            tools=[Tool(name=key) for key in self._tool_keys],
            system_prompt=system_prompt(spec, tools, tools.context.redactor),
            include_default_tools=list(DEFAULT_TOOLS),
            tool_concurrency_limit=1,
        )
        conversation = _ZConversation(
            agent=agent,
            workspace=str(tools.context.workspace.root),
            persistence_dir=str(root),
            conversation_id=self.conversation_id(spec.job_id),
            callbacks=[self._translator.on_event],
            max_iteration_per_run=1,
            stuck_detection=True,
            visualizer=None,
            delete_on_close=False,
        )
        conversation.set_security_analyzer(
            EnsembleSecurityAnalyzer(
                analyzers=[LLMSecurityAnalyzer(), PatternSecurityAnalyzer(), PolicyRailSecurityAnalyzer()]
            )
        )
        conversation.set_confirmation_policy(ConfirmRisky(threshold=SecurityRisk.HIGH, confirm_unknown=False))
        if self.resumed:
            # base_state.json keeps the directory of the pod that wrote it; point the state at this one.
            with conversation.state:
                conversation.state.persistence_dir = str(root / conversation.id.hex)
            # The SDK copies the restored metrics into the agent's LLM only when the agent gets ready (first run()
            # or send_message()), so the responses already counted are read from the restored state itself.
            self._seen_responses = {u.response_id for u in conversation.agent.llm.metrics.token_usages}
            for metrics in conversation.conversation_stats.usage_to_metrics.values():
                self._seen_responses.update(u.response_id for u in metrics.token_usages)
            if conversation.state.execution_status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION:
                # A resumed job never executes an action that waited for confirmation: no decision is carried by
                # the job spec, so the safe default is to reject it (the agent is told and can ask a human).
                conversation.reject_pending_actions("Not confirmed by a human; the job was resumed without approval.")
        self._conversation = conversation

    @staticmethod
    def conversation_id(job_id: str) -> uuid.UUID:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"z-job:{job_id}")

    def step(self) -> StepResult:
        conversation = self._require()
        assert self._translator is not None
        self._translator.begin_step()
        error: str | None = None
        error_class: str | None = None
        try:
            # `run` and `send_message` are wrapped by the SDK's `observe` decorator, which erases their types.
            cast(Any, conversation).run()
        except ConversationRunError as exc:
            cause = exc.__cause__ or exc
            error = f"{type(cause).__name__}: {cause}"
            error_class = classify_llm_error(cause)
        stats = self._translator.begin_step()
        tokens_in, tokens_out = self._emit_usage()
        status = conversation.state.execution_status
        result = StepResult(
            status="continue",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            final_message=stats.final_message,
            tool_calls=stats.tool_calls,
            tool_failures=stats.failures,
            tool_successes=stats.successes,
            validation_errors=stats.validation_errors,
            # a successful call is not progress by itself: new calls count in the LoopDetector, edits in ctx.progress
            progress=bool(stats.agent_messages),
        )
        if error or stats.error:
            result.status = "error"
            result.error = error or stats.error
            result.error_class = error_class or "agent"
        elif status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION:
            result.status = "confirmation_required"
        elif status == ConversationExecutionStatus.STUCK:
            result.status = "stuck"
        elif status == ConversationExecutionStatus.FINISHED:
            result.status = "finished"
        return result

    def checkpoint(self) -> bytes:
        """tar.gz of the conversation persistence directory, every text file redacted (the blob leaves the
        runner: a secret the model itself typed must not be stored by z-server)."""
        conversation = self._require()
        assert self._persistence_root is not None and self._redactor is not None
        conv_dir = self._persistence_root / conversation.id.hex
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for path in sorted(conv_dir.rglob("*")):
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    continue
                arcname = path.relative_to(self._persistence_root).as_posix()
                if path.is_dir():
                    info = tarfile.TarInfo(arcname)
                    info.type, info.mode = tarfile.DIRTYPE, 0o700
                    tar.addfile(info)
                    continue
                data = self._redact_file(path)
                info = tarfile.TarInfo(arcname)
                info.size, info.mode = len(data), 0o600
                tar.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def _redact_file(self, path: Path) -> bytes:
        """A persistence file, redacted. JSON files are redacted value by value and re-serialized: redacting the
        raw text could cut an escape sequence (`\\"`) and leave a file the SDK cannot load on resume."""
        assert self._redactor is not None
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return data
        if path.suffix == ".json":
            try:
                parsed = json.loads(text)
            except ValueError:
                pass
            else:
                return json.dumps(self._redactor.strings(parsed), ensure_ascii=False).encode("utf-8")
        return self._redactor.text(text).encode("utf-8")

    def inject_message(self, text: str) -> None:
        cast(Any, self._require()).send_message(text)

    def stop(self, reason: str) -> None:
        log.info("stopping the agent: %s", reason)
        if self._conversation is not None:
            try:
                self._conversation.close()
            finally:
                self._conversation = None

    # ------------------------------------------------------------------ SupportsModelSwitch
    def set_llm(self, model: str, api_key: str) -> None:
        conversation = self._require()
        self._model, self._api_key = model, api_key
        conversation.switch_llm(self._llm(model, api_key))

    @property
    def model(self) -> str:
        return self._model

    # ------------------------------------------------------------------ internals
    def _require(self) -> LocalConversation:
        if self._conversation is None:
            raise RuntimeError("the backend is not started")
        return self._conversation

    def _llm(self, alias: str, api_key: str) -> LLM:
        assert self._spec is not None
        remaining = (self._spec.deadline - datetime.now(UTC)).total_seconds()
        return LLM(
            model=litellm_model(alias),
            base_url=self._spec.llm.base_url,
            api_key=SecretStr(api_key),
            usage_id=USAGE_ID,
            num_retries=2,
            retry_min_wait=2,
            retry_max_wait=10,
            timeout=int(max(30, min(300, remaining))),
            input_cost_per_token=0.0,
            output_cost_per_token=0.0,
            stream=False,
            log_completions=False,
            native_tool_calling=True,
        )

    @staticmethod
    def _register(spec: JobSpec, tools: ToolSet, context: ToolContext, recorder: ValidationRecorder) -> list[str]:
        keys = []
        for tool in tools:
            key = f"z:{spec.job_id}:{tool.name}"
            register_tool(key, ZSdkTool.build(tool, context, recorder))
            keys.append(key)
        return keys

    def _emit_usage(self) -> tuple[int, int]:
        conversation = self._require()
        metrics = conversation.agent.llm.metrics
        latencies = {r.response_id: r.latency for r in metrics.response_latencies}
        tokens_in = tokens_out = 0
        for usage in metrics.token_usages:
            if usage.response_id in self._seen_responses:
                continue
            self._seen_responses.add(usage.response_id)
            tokens_in += usage.prompt_tokens
            tokens_out += usage.completion_tokens
            payload: dict[str, Any] = {
                # The SDK keeps one metrics object per usage id across switch_llm(), so TokenUsage.model can be
                # stale after an escalation; the alias in use for this step is authoritative.
                "model": self._model,
                "tokens_in": usage.prompt_tokens,
                "tokens_out": usage.completion_tokens,
                "cached_tokens": usage.cache_read_tokens,
            }
            if usage.response_id in latencies:
                payload["latency_ms"] = int(latencies[usage.response_id] * 1000)
            if self._sink is not None:
                self._sink.emit("usage", payload)
        return tokens_in, tokens_out
