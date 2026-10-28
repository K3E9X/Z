"""`z-runner --job <id>`: run one agent job and exit (docs/04 §1, contracts/README.md "Runner protocol").

Environment: `Z_SERVER_URL`, `Z_JOB_ID`, `Z_JOB_TOKEN` (single-use bootstrap token, removed from the process
environment as soon as it is read). Exit codes: see runner/README.md.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import signal
import sys
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    redactor = Redactor()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_RedactingFilter(redactor))
    logging.basicConfig(level=args.log_level.upper(), handlers=[handler], force=True)
    log = logging.getLogger("z_runner")

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
