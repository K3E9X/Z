"""`terminal` (sandboxed shell) and `code` (sandboxed Python), both in the workspace, without credentials.

Commands run in a fresh process group with a scrubbed environment (no Z_* variables, no tokens), stdin closed,
a timeout bounded by the job deadline, and every process of the group is killed when the command returns:
nothing keeps running in the background. Network isolation is enforced by the platform (NetworkPolicy /
Docker network); `guardrails.check_command` only gives early, clear feedback.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from z_common.models.enums import ArtifactKind

from z_runner.guardrails import SANDBOX_NETWORK, check_command, network_refusal_message
from z_runner.tools.base import ToolContext, ToolResult, ZTool, clip

TAIL_BYTES = 4096
MAX_CAPTURE = 4 * 1024 * 1024
DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 1800

_PASSTHROUGH = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_TRUSTED_HOST",
    "PIP_CERT",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "NPM_CONFIG_REGISTRY",
    "GOPROXY",
    "GOFLAGS",
    "MAVEN_OPTS",
    "JAVA_HOME",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
)
_SECRETISH_ENV = re.compile(r"(?i)(token|secret|password|passwd|api_?key|credential|auth)")


def sandbox_home() -> Path:
    base = Path(os.environ.get("Z_RUNNER_TMP") or tempfile.gettempdir()) / "z-sandbox-home"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def sandbox_env() -> dict[str, str]:
    """Environment for agent-controlled processes: an allowlist, never Z_* or anything secret-looking."""
    env = {k: v for k, v in os.environ.items() if k in _PASSTHROUGH and not _SECRETISH_ENV.search(k)}
    home = sandbox_home()
    env.update(
        {
            "HOME": str(home),
            "TMPDIR": str(home),
            "TERM": "dumb",
            "PAGER": "cat",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


@dataclass
class ProcessOutcome:
    exit_code: int | None
    output: str
    timed_out: bool
    truncated: bool


def _read_capture(fh: IO[bytes]) -> tuple[str, bool]:
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    fh.seek(0)
    if size <= MAX_CAPTURE:
        return fh.read().decode("utf-8", errors="replace"), False
    head = fh.read(MAX_CAPTURE // 2)
    fh.seek(size - MAX_CAPTURE // 2)
    tail = fh.read()
    omitted = size - len(head) - len(tail)
    text = (
        head.decode("utf-8", errors="replace")
        + f"\n…[{omitted} bytes omitted]…\n"
        + tail.decode("utf-8", errors="replace")
    )
    return text, True


def run_process(argv: list[str], cwd: Path, timeout_s: float, env: dict[str, str] | None = None) -> ProcessOutcome:
    with tempfile.TemporaryFile(dir=sandbox_home()) as capture:
        proc = subprocess.Popen(  # noqa: S603 (argv built by the runner; the agent controls the script only)
            argv,
            cwd=cwd,
            env=env if env is not None else sandbox_env(),
            stdin=subprocess.DEVNULL,
            stdout=capture,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        timed_out = False
        try:
            proc.wait(timeout=max(1.0, timeout_s))
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        output, truncated = _read_capture(capture)
    return ProcessOutcome(None if timed_out else proc.returncode, output, timed_out, truncated)


def _timeout(args: dict[str, Any], ctx: ToolContext) -> float:
    requested = float(args.get("timeout_s", DEFAULT_TIMEOUT_S))
    return max(1.0, min(requested, MAX_TIMEOUT_S, ctx.remaining_s() - 5.0))


class TerminalTool(ZTool):
    name = "terminal"
    kind = "terminal"
    description = (
        "Run a bash command in the workspace directory and return its combined output and exit code. Each call "
        "starts a fresh shell in the workspace (no state is kept between calls; chain with &&). No network access, "
        "no credentials, no background processes."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {"type": "string", "minLength": 1, "description": "The bash command line."},
            "timeout_s": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT_S, "description": "Default 120."},
        },
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = str(args["command"])
        denial = check_command(command)
        if denial is not None:
            ctx.deny(denial.what, denial.rule, f"{denial.detail} (command not run: {command[:500]})")
            return ToolResult(text=network_refusal_message(denial), ok=False, summary=f"refused ({SANDBOX_NETWORK})")
        outcome = run_process(["/bin/bash", "-c", command], ctx.workspace.root, _timeout(args, ctx))
        output = ctx.redactor.text(outcome.output)
        tail = output.encode("utf-8")[-TAIL_BYTES:].decode("utf-8", errors="ignore")
        output_ref = None
        if len(output.encode("utf-8")) > TAIL_BYTES:
            with self._lock:
                self._count += 1
                index = self._count
            output_ref = ctx.artifacts.try_upload(
                ArtifactKind.terminal, f"terminal-{index:04d}.log", output.encode("utf-8"), "text/plain"
            )
        payload: dict[str, Any] = {"command": command, "exit_code": outcome.exit_code, "output_tail": tail}
        if output_ref:
            payload["output_ref"] = output_ref
        ctx.sink.emit("terminal", payload)
        status = "timed out" if outcome.timed_out else f"exit code {outcome.exit_code}"
        text = f"[{status}]\n{clip(output)}"
        return ToolResult(
            text=text,
            ok=outcome.exit_code == 0,
            summary=f"{status}: {tail.strip().splitlines()[-1][:200] if tail.strip() else '(no output)'}",
            artifact_ref=output_ref,
        )


_NET_IMPORT = re.compile(
    r"(?m)^\s*(?:import|from)\s+(socket|urllib|urllib3|requests|httpx|http\.client|ftplib|smtplib|telnetlib|paramiko|aiohttp|websocket|websockets)\b"
)


class CodeTool(ZTool):
    name = "code"
    kind = "code"
    description = (
        "Run a Python 3 script in the workspace directory (for analysis, charts and file conversion) and return "
        "its output. Standard library plus the data and document libraries installed in the runner image. "
        "No network access. Write files to the workspace to keep them."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code"],
        "properties": {
            "code": {"type": "string", "minLength": 1, "description": "The Python source to run."},
            "timeout_s": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT_S, "description": "Default 120."},
        },
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        code = str(args["code"])
        match = _NET_IMPORT.search(code)
        if match:
            detail = f"the `{match.group(1)}` module is for network access, which the sandbox does not allow"
            ctx.deny(f"python import {match.group(1)}", SANDBOX_NETWORK, detail)
            return ToolResult(
                text=f"Refused by the platform guardrail `{SANDBOX_NETWORK}`: {detail}. The script was NOT run.",
                ok=False,
                summary=f"refused ({SANDBOX_NETWORK})",
            )
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=sandbox_home(), delete=False, encoding="utf-8") as fh:
            fh.write(code)
            script = fh.name
        try:
            outcome = run_process([sys.executable, "-I", "-B", script], ctx.workspace.root, _timeout(args, ctx))
        finally:
            Path(script).unlink(missing_ok=True)
        output = ctx.redactor.text(outcome.output)
        status = "timed out" if outcome.timed_out else f"exit code {outcome.exit_code}"
        return ToolResult(text=f"[{status}]\n{clip(output)}", ok=outcome.exit_code == 0, summary=f"python: {status}")
