"""Pick the executor from settings (`executor.kind` in config/z.yaml, or Z_EXECUTOR)."""

from __future__ import annotations

import os

from z_server.executors import Executor, ExecutorError
from z_server.executors.docker import DockerExecutor
from z_server.executors.kubernetes import KubernetesExecutor
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
    if settings.executor == "kubernetes":
        kube = config.get("kubernetes", {})
        env = os.environ
        return KubernetesExecutor(
            image=settings.runner_image,
            namespace=env.get("Z_RUNNER_NAMESPACE") or str(kube.get("namespace", "z-runners")),
            service_account=env.get("Z_RUNNER_SERVICE_ACCOUNT") or str(kube.get("service_account", "z-runner")),
            runtime_class=env.get("Z_RUNNER_RUNTIME_CLASS") or kube.get("runtime_class") or None,
            job_ttl_s=int(env.get("Z_RUNNER_JOB_TTL_S") or kube.get("job_ttl_s", 3600)),
        )
    raise ExecutorError(f"unknown executor {settings.executor!r} (docker, kubernetes or process)")
