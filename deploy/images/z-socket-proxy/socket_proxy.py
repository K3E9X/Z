"""Docker socket proxy for the Z docker executor (docs/07 §4, invariant 13).

z-server never sees the Docker socket. This proxy forwards only what the executor needs (create, start, stop,
inspect, logs, remove of runner containers) and checks every `create` body against the runner hardening, so a
compromised z-server still cannot start a privileged container, mount the host, join another network or run
another image. Standard library only; one file.

Environment:
  Z_RUNNER_IMAGES     comma-separated images that may be started (exact match, pin by digest)
  Z_RUNNER_NETWORK    network runners join (default z_runners)
  Z_ALLOWED_RUNTIMES  optional extra OCI runtimes, e.g. runsc (gVisor)
  Z_MAX_MEMORY, Z_MAX_NANO_CPUS, Z_MAX_PIDS  ceilings (bytes, 1e-9 CPUs, processes)
  DOCKER_SOCKET       default /var/run/docker.sock;  LISTEN default 0.0.0.0:2375
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import socket
import sys
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger("z.socket-proxy")
LABEL = "io.z.job"
VERSION = re.compile(r"^/v1\.\d{2}(?=/)")
CONTAINER = re.compile(
    r"^/containers/(?P<id>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})(?P<rest>/json|/logs|/start|/stop|/wait)?$"
)
NAME = re.compile(r"^z-job-[a-z0-9-]{1,80}$")
TOP_KEYS = {"Image", "Cmd", "Env", "User", "WorkingDir", "Labels", "HostConfig"}
HOST_KEYS = {
    "NetworkMode",
    "ReadonlyRootfs",
    "CapDrop",
    "SecurityOpt",
    "Tmpfs",
    "Memory",
    "NanoCpus",
    "PidsLimit",
    "Privileged",
    "RestartPolicy",
    "Runtime",
}
TMPFS_PATHS = {"/workspace", "/tmp"}  # noqa: S108 - tmpfs mounts inside the runner container
MAX_BODY = 1024 * 1024


class Refused(Exception):
    pass


@dataclass
class Policy:
    images: set[str]
    network: str = "z_runners"
    runtimes: set[str] = field(default_factory=set)
    max_memory: int = 8 * 1024**3
    max_nano_cpus: int = 8_000_000_000
    max_pids: int = 4096

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Policy:
        e = os.environ if env is None else env
        images = {i.strip() for i in e.get("Z_RUNNER_IMAGES", "").split(",") if i.strip()}
        if not images:
            raise SystemExit("Z_RUNNER_IMAGES is required")
        return cls(
            images=images,
            network=e.get("Z_RUNNER_NETWORK", "z_runners"),
            runtimes={r.strip() for r in e.get("Z_ALLOWED_RUNTIMES", "").split(",") if r.strip()},
            max_memory=int(e.get("Z_MAX_MEMORY", 8 * 1024**3)),
            max_nano_cpus=int(e.get("Z_MAX_NANO_CPUS", 8_000_000_000)),
            max_pids=int(e.get("Z_MAX_PIDS", 4096)),
        )

    def check_create(self, query: dict[str, list[str]], body: Any) -> None:
        name = (query.get("name") or [""])[0]
        if not NAME.match(name):
            raise Refused("container name must be z-job-<id>")
        if not isinstance(body, dict):
            raise Refused("body must be a JSON object")
        if unknown := set(body) - TOP_KEYS:
            raise Refused(f"keys not allowed: {sorted(unknown)}")
        if body.get("Image") not in self.images:
            raise Refused("image is not an allowed runner image")
        if body.get("User") != "10001:10001":
            raise Refused("runners run as 10001:10001")
        cmd = body.get("Cmd")
        if not (isinstance(cmd, list) and len(cmd) == 2 and cmd[0] == "--job" and isinstance(cmd[1], str)):
            raise Refused("command must be --job <id>")
        labels = body.get("Labels")
        if not (isinstance(labels, dict) and labels.get(LABEL)):
            raise Refused(f"label {LABEL} is required")
        env = body.get("Env") or []
        if not (isinstance(env, list) and all(isinstance(v, str) and "=" in v for v in env)):
            raise Refused("Env must be a list of NAME=value")
        self._check_host(body.get("HostConfig"))

    def _check_host(self, host: Any) -> None:
        if not isinstance(host, dict):
            raise Refused("HostConfig is required")
        if unknown := set(host) - HOST_KEYS:
            raise Refused(f"HostConfig keys not allowed: {sorted(unknown)}")
        checks = [
            (host.get("NetworkMode") == self.network, f"network must be {self.network}"),
            (host.get("ReadonlyRootfs") is True, "read-only root filesystem required"),
            (host.get("CapDrop") == ["ALL"], "every capability must be dropped"),
            (host.get("SecurityOpt") == ["no-new-privileges:true"], "SecurityOpt must be exactly no-new-privileges"),
            (host.get("Privileged") in (None, False), "privileged containers are refused"),
            ((host.get("RestartPolicy") or {"Name": "no"}).get("Name") in ("no", ""), "no restart policy"),
            (host.get("Runtime") in (None, "", *self.runtimes), "runtime not allowed"),
            (_within(host.get("Memory"), self.max_memory), "Memory limit required and within the ceiling"),
            (_within(host.get("NanoCpus"), self.max_nano_cpus), "NanoCpus limit required and within the ceiling"),
            (_within(host.get("PidsLimit"), self.max_pids), "PidsLimit required and within the ceiling"),
        ]
        for ok, reason in checks:
            if not ok:
                raise Refused(reason)
        tmpfs = host.get("Tmpfs") or {}
        if not isinstance(tmpfs, dict) or set(tmpfs) - TMPFS_PATHS:
            raise Refused("tmpfs only on /workspace and /tmp")
        for options in tmpfs.values():
            flags = set(str(options).split(","))
            if not {"nosuid", "nodev"} <= flags:
                raise Refused("tmpfs mounts need nosuid,nodev")


def _within(value: Any, ceiling: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= ceiling


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 30) -> None:
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.path)
        self.sock = sock


@dataclass
class Upstream:
    socket_path: str

    def request(self, method: str, path: str, body: bytes | None = None) -> tuple[int, dict[str, str], bytes]:
        conn = UnixConnection(self.socket_path)
        try:
            headers = {"Content-Type": "application/json"} if body is not None else {}
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()


def route(method: str, raw_path: str) -> tuple[str, str | None, str]:
    """(kind, container id, upstream path). kind: ping | version | create | container | refused."""
    parts = urlsplit(raw_path)
    path = VERSION.sub("", parts.path)
    if method in {"GET", "HEAD"} and path == "/_ping":
        return "ping", None, raw_path
    if method == "GET" and path == "/version":
        return "version", None, raw_path
    if method == "POST" and path == "/containers/create":
        return "create", None, raw_path
    match = CONTAINER.match(path)
    if match:
        rest = match.group("rest") or ""
        allowed = {
            ("GET", "/json"),
            ("GET", "/logs"),
            ("POST", "/start"),
            ("POST", "/stop"),
            ("POST", "/wait"),
            ("DELETE", ""),
        }
        if (method, rest) in allowed:
            return "container", match.group("id"), raw_path
    return "refused", None, raw_path


def make_handler(policy: Policy, upstream: Upstream) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "z-socket-proxy"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:
            log.info("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            for key, value in (headers or {}).items():
                if key.lower() in {"content-type", "api-version", "docker-experimental", "ostype"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _refuse(self, reason: str) -> None:
            log.warning("refused %s %s: %s", self.command, self.path, reason)
            self._send(
                403, json.dumps({"message": f"z-socket-proxy: {reason}"}).encode(), {"Content-Type": "application/json"}
            )

        def _handle(self) -> None:
            kind, container, upstream_path = route(self.command, self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._refuse("body too large")
            body = self.rfile.read(length) if length else None
            try:
                if kind == "refused":
                    raise Refused("operation not allowed")
                if kind == "create":
                    try:
                        parsed = json.loads(body or b"null")
                    except ValueError as exc:
                        raise Refused("body is not JSON") from exc
                    policy.check_create(parse_qs(urlsplit(self.path).query), parsed)
                elif kind == "container":
                    self._require_runner(container or "")
                elif body:
                    raise Refused("unexpected body")
            except Refused as exc:
                return self._refuse(str(exc))
            status, headers, data = upstream.request(self.command, upstream_path, body)
            self._send(status, data, headers)

        def _require_runner(self, container: str) -> None:
            prefix = VERSION.match(urlsplit(self.path).path)
            status, _, data = upstream.request(
                "GET", f"{prefix.group(0) if prefix else ''}/containers/{container}/json"
            )
            if status == 404:
                return  # let the engine answer 404 (stop and remove are idempotent for the executor)
            labels = (json.loads(data or b"{}").get("Config") or {}).get("Labels") or {}
            if status != 200 or not labels.get(LABEL):
                raise Refused("not a Z runner container")

        do_GET = do_POST = do_DELETE = do_HEAD = _handle

        def do_PUT(self) -> None:
            self._refuse("operation not allowed")

        do_PATCH = do_PUT

    return Handler


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    policy = Policy.from_env()
    upstream = Upstream(os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock"))
    host, _, port = os.environ.get("LISTEN", "0.0.0.0:2375").rpartition(":")
    server = ThreadingHTTPServer((host, int(port)), make_handler(policy, upstream))
    log.info("z-socket-proxy on %s:%s, images %s, network %s", host, port, sorted(policy.images), policy.network)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
