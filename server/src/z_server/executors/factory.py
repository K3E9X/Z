"""Pick the executor from settings (`executor.kind` in config/z.yaml, or Z_EXECUTOR)."""

from __future__ import annotations

from z_server.executors import Executor, ExecutorError
from z_server.executors.docker import DockerExecutor
from z_server.executors.process import ProcessExecutor
from z_server.settings import Settings


def from_settings(settings: Settings) -> Executor:
    config = settings.config.get("executor", {})
    if settings.executor == "docker":
        docker = config.get("docker", {})
        return DockerExecutor(
            base_url=str(docker.get("socket_proxy_url", "http://docker-socket-proxy:2375")),
            image=settings.runner_image,
            network=str(docker.get("network", "z_runners")),
            runtime=docker.get("runtime") or None,
        )
    if settings.executor == "process":
        if settings.production:
            raise ExecutorError("the process executor is refused when Z_ENV=production")
        return ProcessExecutor()
    raise ExecutorError(f"executor {settings.executor!r} is not available in this build (kubernetes ships with B8)")
