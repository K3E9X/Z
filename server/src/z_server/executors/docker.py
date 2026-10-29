"""Runner containers through the Docker Engine API, normally via a socket proxy that only allows container
create/start/stop/inspect/logs/remove (docs/07 §4). Same hardening as Kubernetes (invariant 13):
non-root, read-only root filesystem, every capability dropped, no-new-privileges, default seccomp,
tmpfs workspace, runners network only, CPU/memory/pids limits."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any

import httpx

from z_server.executors import MAX_TOOL_OUTPUT, MAX_TOOL_STDERR, ExecutorError, ToolRun

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
class ToolLimits:
    memory_bytes: int = 512 * 1024**2
    nano_cpus: int = 1_000_000_000
    pids: int = 256
    tmp_size: str = "128m"


@dataclass
class DockerExecutor:
    base_url: str  # http://docker-socket-proxy:2375 or unix:///var/run/docker.sock
    image: str
    network: str = "z_runners"
    runtime: str | None = None  # e.g. runsc for gVisor
    limits: DockerLimits = field(default_factory=DockerLimits)
    tool_limits: ToolLimits = field(default_factory=ToolLimits)
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

    def tool_spec(self, run_id: str, image: str, argv: list[str], env: dict[str, str]) -> dict[str, Any]:
        """A cli tool container (socket proxy profile z-tool-*): the runner hardening, user 65534, /tmp only."""
        limits = self.tool_limits
        host: dict[str, Any] = {
            "NetworkMode": self.network,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Tmpfs": {"/tmp": f"rw,nosuid,nodev,noexec,size={limits.tmp_size},uid=65534,gid=65534"},  # noqa: S108
            "Memory": limits.memory_bytes,
            "NanoCpus": limits.nano_cpus,
            "PidsLimit": limits.pids,
            "Privileged": False,
            "RestartPolicy": {"Name": "no"},
            # capped at the source: the tool's output cannot fill the host disk (the proxy checks this exact value)
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "2m", "max-file": "2"}},
        }
        if self.runtime:
            host["Runtime"] = self.runtime
        full_env = {**env, "HOME": "/tmp"}  # noqa: S108 - inside the tool container
        return {
            "Image": image,
            "Cmd": list(argv),
            "Env": [f"{k}={v}" for k, v in sorted(full_env.items())],
            "User": "65534:65534",
            "WorkingDir": "/tmp",  # noqa: S108
            "Labels": {LABEL: f"tool:{run_id}"},
            "HostConfig": host,
        }

    async def run_tool(self, image: str, argv: list[str], env: dict[str, str], timeout_s: float) -> ToolRun:
        run_id = secrets.token_hex(8)
        created = await self._call(
            "POST",
            "/containers/create",
            params={"name": f"z-tool-{run_id}"},
            json=self.tool_spec(run_id, image, argv, env),
        )
        if created.status_code != 201:
            raise ExecutorError(f"tool container create refused ({created.status_code}): {created.text[:300]}")
        container = str(created.json()["Id"])
        try:
            started = await self._call("POST", f"/containers/{container}/start")
            if started.status_code not in {204, 304}:
                raise ExecutorError(f"tool container start refused ({started.status_code}): {started.text[:300]}")
            exit_code: int | None = None
            timed_out = False
            try:
                waited = await asyncio.wait_for(
                    self._call("POST", f"/containers/{container}/wait", timeout=timeout_s + 5), timeout_s
                )
                exit_code = int(waited.json().get("StatusCode", -1)) if waited.status_code == 200 else -1
            except TimeoutError:
                timed_out = True
                await self._call("POST", f"/containers/{container}/stop", params={"t": 1})
            # bounded reads: a tool can write gigabytes in its run; z-server keeps at most MAX_TOOL_OUTPUT of it
            raw_out, truncated = await self._logs(container, {"stdout": 1, "stderr": 0}, MAX_TOOL_OUTPUT)
            raw_err, _ = await self._logs(container, {"stdout": 0, "stderr": 1, "tail": 100}, MAX_TOOL_STDERR)
        finally:
            await self._call("DELETE", f"/containers/{container}", params={"force": "true"})
        stdout = demux(raw_out)
        if len(stdout.encode()) > MAX_TOOL_OUTPUT:
            truncated = True
            stdout = stdout.encode()[:MAX_TOOL_OUTPUT].decode("utf-8", errors="ignore")
        return ToolRun(exit_code, stdout, demux(raw_err)[-4000:], timed_out, truncated)

    async def _logs(self, container: str, params: dict[str, Any], limit: int) -> tuple[bytes, bool]:
        """At most `limit` bytes of payload (plus frame headers) of a container's logs; True when cut."""
        budget = limit + limit // 8 + 8  # room for the 8-byte frame headers
        data = bytearray()
        try:
            async with self.client().stream(
                "GET", f"/{API_VERSION}/containers/{container}/logs", params=params
            ) as resp:
                if resp.status_code != 200:
                    return b"", False
                async for chunk in resp.aiter_bytes():
                    data += chunk
                    if len(data) > budget:
                        return bytes(data[:budget]), True
        except httpx.HTTPError as exc:
            raise ExecutorError(f"Docker API unreachable: {exc}") from exc
        return bytes(data), False


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
