"""Runner containers through the Docker Engine API, normally via a socket proxy that only allows container
create/start/stop/inspect/logs/remove (docs/07 §4). Same hardening as Kubernetes (invariant 13):
non-root, read-only root filesystem, every capability dropped, no-new-privileges, default seccomp,
tmpfs workspace, runners network only, CPU/memory/pids limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from z_server.executors import ExecutorError

API_VERSION = "v1.43"
LABEL = "io.z.job"


@dataclass
class DockerLimits:
    memory_bytes: int = 2 * 1024**3
    nano_cpus: int = 2_000_000_000
    pids: int = 512
    workspace_size: str = "2g"
    tmp_size: str = "256m"


@dataclass
class DockerExecutor:
    base_url: str  # http://docker-socket-proxy:2375 or unix:///var/run/docker.sock
    image: str
    network: str = "z_runners"
    runtime: str | None = None  # e.g. runsc for gVisor
    limits: DockerLimits = field(default_factory=DockerLimits)
    name: str = "docker"
    _client: httpx.AsyncClient | None = None

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            if self.base_url.startswith("unix://"):
                transport = httpx.AsyncHTTPTransport(uds=self.base_url.removeprefix("unix://"))
                self._client = httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=30)
            else:
                self._client = httpx.AsyncClient(base_url=self.base_url.rstrip("/"), timeout=30)
        return self._client

    def container_spec(self, job_id: str, env: dict[str, str]) -> dict[str, Any]:
        host: dict[str, Any] = {
            "NetworkMode": self.network,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": {
                "/workspace": f"rw,nosuid,nodev,size={self.limits.workspace_size},uid=10001,gid=10001,mode=0700",
                "/tmp": f"rw,nosuid,nodev,noexec,size={self.limits.tmp_size},uid=10001,gid=10001",  # noqa: S108 - tmpfs mount inside the container
            },
            "Memory": self.limits.memory_bytes,
            "NanoCpus": self.limits.nano_cpus,
            "PidsLimit": self.limits.pids,
            "Privileged": False,
            "RestartPolicy": {"Name": "no"},
        }
        if self.runtime:
            host["Runtime"] = self.runtime
        return {
            "Image": self.image,
            "Cmd": ["--job", job_id],
            "Env": [f"{k}={v}" for k, v in sorted(env.items())],
            "User": "10001:10001",
            "WorkingDir": "/workspace",
            "Labels": {LABEL: job_id},
            "HostConfig": host,
        }

    async def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = await self.client().request(method, f"/{API_VERSION}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise ExecutorError(f"Docker API unreachable: {exc}") from exc
        return resp

    async def launch(self, job_id: str, env: dict[str, str]) -> str:
        name = f"z-job-{job_id.lower().replace('_', '-')}"
        created = await self._call(
            "POST", "/containers/create", params={"name": name}, json=self.container_spec(job_id, env)
        )
        if created.status_code != 201:
            raise ExecutorError(f"container create refused ({created.status_code}): {created.text[:300]}")
        container = str(created.json()["Id"])
        started = await self._call("POST", f"/containers/{container}/start")
        if started.status_code not in {204, 304}:
            await self.stop(container)
            raise ExecutorError(f"container start refused ({started.status_code}): {started.text[:300]}")
        return container

    async def is_running(self, ref: str) -> bool:
        resp = await self._call("GET", f"/containers/{ref}/json")
        if resp.status_code == 404:
            return False
        if resp.status_code != 200:
            raise ExecutorError(f"inspect failed ({resp.status_code})")
        return bool(resp.json().get("State", {}).get("Running"))

    async def logs(self, ref: str, tail: int = 80) -> str:
        try:
            resp = await self._call("GET", f"/containers/{ref}/logs", params={"stdout": 1, "stderr": 1, "tail": tail})
            state = await self._call("GET", f"/containers/{ref}/json")
        except ExecutorError:
            return ""
        if resp.status_code != 200:
            return ""
        text = demux(resp.content)
        if state.status_code == 200:
            exit_code = state.json().get("State", {}).get("ExitCode")
            text += f"\n[exit code {exit_code}]"
        return text

    async def stop(self, ref: str) -> None:
        await self._call("POST", f"/containers/{ref}/stop", params={"t": 10})
        await self._call("DELETE", f"/containers/{ref}", params={"force": "true"})


def demux(raw: bytes) -> str:
    """Docker multiplexes stdout/stderr of non-TTY containers in frames: 1 byte stream, 3 zero, 4 size (BE)."""
    out = []
    i = 0
    while i + 8 <= len(raw) and raw[i] in (0, 1, 2) and raw[i + 1 : i + 4] == b"\0\0\0":
        size = int.from_bytes(raw[i + 4 : i + 8], "big")
        out.append(raw[i + 8 : i + 8 + size])
        i += 8 + size
    if i < len(raw):
        out.append(raw[i:])
    return b"".join(out).decode("utf-8", errors="replace")
