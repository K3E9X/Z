"""`z-runner --job <id>`: run one agent job and exit (docs/04 §1, contracts/README.md "Runner protocol").

Environment: `Z_SERVER_URL`, `Z_JOB_ID`, `Z_JOB_TOKEN` (single-use bootstrap token, removed from the process
environment as soon as it is read). Exit codes: see runner/README.md.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import logging
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType

from z_runner import __version__
from z_runner.redaction import Redactor
from z_runner.runner import EXIT_USAGE, Runner, RunnerConfig

PR_SET_DUMPABLE = 4


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"level": record.levelname.lower(), "logger": record.name, "message": record.getMessage()}
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


class _RedactingFilter(logging.Filter):
    """Last line of defense: no secret in any log line, whoever logs it."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self.redactor.text(message)
        if redacted != message or record.args:
            record.msg, record.args = redacted, None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = self.redactor.text(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def _no_core_dumps() -> None:
    """Make the process non-dumpable: agent-controlled processes (same uid) cannot read its memory or
    environment through /proc (session token, LLM key)."""
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
        except (OSError, AttributeError):
            logging.getLogger("z_runner").warning("prctl(PR_SET_DUMPABLE) is not available")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="z-runner",
        description="Run one Z agent job and exit. Reads Z_SERVER_URL, Z_JOB_ID and Z_JOB_TOKEN from the environment.",
        epilog="Exit codes: 0 succeeded, 1 failed, 2 usage error, 3 needs_attention, 4 budget_exceeded, "
        "5 timed_out, 6 cancelled, 10 bootstrap failed, 11 completion not recorded, 12 terminated (checkpoint kept).",
    )
    parser.add_argument("--job", help="job id (default: $Z_JOB_ID)")
    parser.add_argument("--workspace", default=os.environ.get("Z_WORKSPACE", "/workspace"), help="workspace directory")
    parser.add_argument("--state-dir", default=os.environ.get("Z_RUNNER_STATE"), help="private state directory")
    parser.add_argument("--log-level", default=os.environ.get("Z_LOG_LEVEL", "INFO"))
    parser.add_argument("--version", action="version", version=f"z-runner {__version__}")
    return parser


_FORWARDED = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT, signal.SIGUSR1, signal.SIGUSR2)


def _init_process(run: Callable[[], int]) -> int:
    """PID 1 of a container: run the job in a child, forward signals to it and reap every orphan.

    Processes started by the agent can leave orphans that the kernel re-parents to PID 1; without an init they
    stay zombies. This is what tini does, without an extra binary in the image.
    """
    _no_core_dumps()  # the initial environment of PID 1 (bootstrap token) must not be readable either
    child = os.fork()
    if child == 0:  # the runner
        code = 1
        try:
            code = run()
        except SystemExit as exc:  # argparse --help / --version
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        finally:
            logging.shutdown()
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(code)

    def forward(signum: int, frame: FrameType | None) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, signum)

    for sig in _FORWARDED:
        signal.signal(sig, forward)
    while True:
        try:
            pid, status = os.wait()
        except ChildProcessError:
            return 1
        if pid == child:
            code = os.waitstatus_to_exitcode(status)
            return code if code >= 0 else 128 - code


def main(argv: list[str] | None = None) -> int:
    if os.getpid() == 1 and os.environ.get("Z_RUNNER_NO_INIT") != "1":
        return _init_process(lambda: _main(argv))
    return _main(argv)


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    redactor = Redactor()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_RedactingFilter(redactor))
    logging.basicConfig(level=args.log_level.upper(), handlers=[handler], force=True)
    log = logging.getLogger("z_runner")
    for chatty in ("httpx", "httpcore", "mcp", "LiteLLM", "litellm"):
        logging.getLogger(chatty).setLevel(max(logging.WARNING, logging.getLogger().level))

    server_url = os.environ.get("Z_SERVER_URL", "")
    env_job = os.environ.get("Z_JOB_ID", "")
    token = os.environ.pop("Z_JOB_TOKEN", "")
    job_id = args.job or env_job
    if not server_url or not job_id or not token:
        log.error("Z_SERVER_URL, the job id (--job or Z_JOB_ID) and Z_JOB_TOKEN are required")
        return EXIT_USAGE
    if args.job and env_job and args.job != env_job:
        log.error("--job does not match Z_JOB_ID")
        return EXIT_USAGE
    redactor.add_secret(token)
    _no_core_dumps()

    config = RunnerConfig(
        workspace_dir=Path(args.workspace), state_dir=Path(args.state_dir) if args.state_dir else None
    )
    runner = Runner(server_url, job_id, token, config, redactor=redactor)

    def on_term(signum: int, frame: FrameType | None) -> None:
        log.warning("signal %d: stopping after the current step", signum)
        runner.request_termination()

    signal.signal(signal.SIGTERM, on_term)
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())
