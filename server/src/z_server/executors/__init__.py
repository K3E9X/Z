"""Executors start one z-runner per job (docs/02 §1): docker (socket proxy), process (dev only), kubernetes."""

from __future__ import annotations

from typing import Protocol


class ExecutorError(RuntimeError):
    pass


class Executor(Protocol):
    name: str

    async def launch(self, job_id: str, env: dict[str, str]) -> str:
        """Start `z-runner --job <job_id>` with `env`; return an executor reference."""

    async def is_running(self, ref: str) -> bool: ...

    async def stop(self, ref: str) -> None:
        """Stop and clean up. Idempotent."""

    async def logs(self, ref: str, tail: int = 80) -> str:
        """Last lines of the runner's output (best effort, "" when unavailable), for diagnosing a runner that died."""
