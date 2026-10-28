"""Runner as a local subprocess. Development only: refused when Z_ENV=production (settings.check)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import tempfile
from pathlib import Path


class ProcessExecutor:
    name = "process"

    def __init__(self, command: list[str] | None = None, workdir: Path | None = None) -> None:
        self.command = command or [shutil.which("z-runner") or "z-runner"]
        self.workdir = workdir or Path(tempfile.gettempdir()) / "z-jobs"
        self._procs: dict[str, asyncio.subprocess.Process] = {}

    async def launch(self, job_id: str, env: dict[str, str]) -> str:
        workspace = self.workdir / job_id
        workspace.mkdir(parents=True, exist_ok=True)
        base = {
            k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}
        }
        proc = await asyncio.create_subprocess_exec(
            *self.command,
            "--job",
            job_id,
            env={**base, **env, "Z_WORKSPACE": str(workspace)},
            cwd=workspace,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._procs[job_id] = proc
        return job_id

    async def is_running(self, ref: str) -> bool:
        proc = self._procs.get(ref)
        return proc is not None and proc.returncode is None

    async def stop(self, ref: str) -> None:
        proc = self._procs.pop(ref, None)
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
