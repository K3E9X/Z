"""Executors start one z-runner per job (docs/02 §1): docker (socket proxy), process (dev only), kubernetes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ExecutorError(RuntimeError):
    pass


MAX_TOOL_OUTPUT = 1024 * 1024  # bytes of stdout kept from a cli tool run (the rest is cut, `truncated` is set)
MAX_TOOL_STDERR = 64 * 1024  # bytes of the end of stderr read (the last 4000 characters are kept)


@dataclass
class ToolRun:
    """One cli connector call (ADR 0025): a tool container run to completion."""

    exit_code: int | None  # None when it was stopped at the timeout
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


class ToolSandbox(Protocol):
    async def run_tool(self, image: str, argv: list[str], env: dict[str, str], timeout_s: float) -> ToolRun:
        """Run `image` with `argv` as its command (after the ENTRYPOINT, if any) in a hardened, network-restricted container."""


class Executor(Protocol):
    name: str

    async def launch(self, job_id: str, env: dict[str, str]) -> str:
        """Start `z-runner --job <job_id>` with `env`; return an executor reference."""

    async def is_running(self, ref: str) -> bool: ...

    async def stop(self, ref: str) -> None:
        """Stop and clean up. Idempotent."""

    async def logs(self, ref: str, tail: int = 80) -> str:
        """Last lines of the runner's output (best effort, "" when unavailable), for diagnosing a runner that died."""
