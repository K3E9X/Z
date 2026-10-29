"""Runner pods through the Kubernetes API (docs/11 §2), in-cluster, with the z-server ServiceAccount.

One batch/v1 Job per Z job in the runners namespace (default `z-runners`): `backoffLimit: 0` (Z retries, not
Kubernetes), `ttlSecondsAfterFinished` for cleanup, `activeDeadlineSeconds` from the job timeout. The job
token travels in a per-job Secret owned by the Job (deleted with it), never in the pod spec. Hardening matches
the Docker executor and the `restricted` Pod Security Standard (invariant 13): non-root uid 10001, read-only
root filesystem, emptyDir workspace and /tmp, no privilege escalation, every capability dropped, seccomp
RuntimeDefault, no service account token, no host namespaces, optional RuntimeClass (gVisor).

The Role the chart grants z-server in the runners namespace: jobs create/get/list/delete, pods get/list,
pods/log get, secrets create/get/delete/patch. Only the Kubernetes REST API over HTTPS is used (no client
library), with the pod's service account token and CA, so the same code runs on k3s and any conformant cluster.
"""

from __future__ import annotations

import asyncio
import base64
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from z_server.executors import MAX_TOOL_OUTPUT, ExecutorError, ToolRun

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
LABEL = "z.io/job"
SECRET_KEYS = ("Z_JOB_TOKEN",)  # env entries that go to the per-job Secret instead of the pod spec


@dataclass
class KubeLimits:
    cpu: str = "2"
    memory: str = "2Gi"
    cpu_request: str = "250m"
    memory_request: str = "512Mi"
    workspace_size: str = "2Gi"
    tmp_size: str = "256Mi"
    ephemeral_storage: str = "3Gi"


@dataclass
class KubernetesExecutor:
    image: str
    namespace: str = "z-runners"
    service_account: str = "z-runner"
    runtime_class: str | None = None
    job_ttl_s: int = 3600
    deadline_s: int = 7200
    api_url: str | None = None  # default: https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT
    token: str | None = None  # default: the pod's service account token
    ca_file: str | None = None
    limits: KubeLimits = field(default_factory=KubeLimits)
    name: str = "kubernetes"
    transport: httpx.AsyncBaseTransport | None = None  # tests
    _client: httpx.AsyncClient | None = None

    # ---------------------------------------------------------------- client

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            import os

            base = self.api_url or (
                f"https://{os.environ.get('KUBERNETES_SERVICE_HOST', 'kubernetes.default.svc')}:"
                f"{os.environ.get('KUBERNETES_SERVICE_PORT', '443')}"
            )
            token = self.token
            if token is None and (SA_DIR / "token").exists():
                token = (SA_DIR / "token").read_text().strip()
            ca = self.ca_file or (str(SA_DIR / "ca.crt") if (SA_DIR / "ca.crt").exists() else None)
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            if self.transport is not None:
                self._client = httpx.AsyncClient(base_url=base, headers=headers, transport=self.transport, timeout=30)
            else:
                self._client = httpx.AsyncClient(base_url=base, headers=headers, verify=ca or True, timeout=30)
        return self._client

    async def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client().request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ExecutorError(f"Kubernetes API unreachable: {exc}") from exc

    # ---------------------------------------------------------------- manifests

    @staticmethod
    def job_name(job_id: str) -> str:
        return ("z-job-" + re.sub(r"[^a-z0-9-]", "-", job_id.lower()))[:63].rstrip("-")

    def secret_manifest(self, job_id: str, env: dict[str, str]) -> dict[str, Any]:
        data = {k: base64.b64encode(v.encode()).decode() for k, v in env.items() if k in SECRET_KEYS}
        return {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": self.job_name(job_id),
                "namespace": self.namespace,
                "labels": {LABEL: self.job_name(job_id)},
            },
            "type": "Opaque",
            "data": data,
        }

    def job_manifest(self, job_id: str, env: dict[str, str]) -> dict[str, Any]:
        name = self.job_name(job_id)
        plain = [{"name": k, "value": v} for k, v in sorted(env.items()) if k not in SECRET_KEYS]
        secret_env = [
            {"name": k, "valueFrom": {"secretKeyRef": {"name": name, "key": k}}} for k in SECRET_KEYS if k in env
        ]
        lim = self.limits
        pod: dict[str, Any] = {
            "restartPolicy": "Never",
            "serviceAccountName": self.service_account,
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "hostNetwork": False,
            "hostPID": False,
            "hostIPC": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "runAsGroup": 10001,
                "fsGroup": 10001,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "runner",
                    "image": self.image,
                    "imagePullPolicy": "IfNotPresent",
                    "args": ["--job", job_id],
                    "workingDir": "/workspace",
                    "env": plain + secret_env,
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "privileged": False,
                        "runAsNonRoot": True,
                        "capabilities": {"drop": ["ALL"]},
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "resources": {
                        "limits": {"cpu": lim.cpu, "memory": lim.memory, "ephemeral-storage": lim.ephemeral_storage},
                        "requests": {"cpu": lim.cpu_request, "memory": lim.memory_request},
                    },
                    "volumeMounts": [
                        {"name": "workspace", "mountPath": "/workspace"},
                        {"name": "tmp", "mountPath": "/tmp"},  # noqa: S108 - emptyDir inside the pod
                    ],
                }
            ],
            "volumes": [
                {"name": "workspace", "emptyDir": {"sizeLimit": lim.workspace_size}},
                {"name": "tmp", "emptyDir": {"sizeLimit": lim.tmp_size}},
            ],
        }
        if self.runtime_class:
            pod["runtimeClassName"] = self.runtime_class
        labels = {LABEL: name, "app.kubernetes.io/name": "z-runner", "app.kubernetes.io/part-of": "z"}
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": name, "namespace": self.namespace, "labels": labels},
            "spec": {
                "backoffLimit": 0,
                "ttlSecondsAfterFinished": self.job_ttl_s,
                "activeDeadlineSeconds": self.deadline_s,
                "template": {"metadata": {"labels": labels}, "spec": pod},
            },
        }

    # ---------------------------------------------------------------- Executor protocol

    async def launch(self, job_id: str, env: dict[str, str]) -> str:
        name = self.job_name(job_id)
        ns = self.namespace
        secret = await self._call("POST", f"/api/v1/namespaces/{ns}/secrets", json=self.secret_manifest(job_id, env))
        if secret.status_code == 409:  # a previous attempt left it: replace its content
            await self._call("DELETE", f"/api/v1/namespaces/{ns}/secrets/{name}")
            secret = await self._call(
                "POST", f"/api/v1/namespaces/{ns}/secrets", json=self.secret_manifest(job_id, env)
            )
        if secret.status_code not in (200, 201):
            raise ExecutorError(f"secret create refused ({secret.status_code}): {secret.text[:300]}")
        created = await self._call("POST", f"/apis/batch/v1/namespaces/{ns}/jobs", json=self.job_manifest(job_id, env))
        if created.status_code not in (200, 201):
            await self._call("DELETE", f"/api/v1/namespaces/{ns}/secrets/{name}")
            raise ExecutorError(f"job create refused ({created.status_code}): {created.text[:300]}")
        uid = created.json().get("metadata", {}).get("uid")
        if uid:  # the Secret goes away with the Job (garbage collector)
            owner = {"apiVersion": "batch/v1", "kind": "Job", "name": name, "uid": uid, "blockOwnerDeletion": False}
            await self._call(
                "PATCH",
                f"/api/v1/namespaces/{ns}/secrets/{name}",
                json={"metadata": {"ownerReferences": [owner]}},
                headers={"Content-Type": "application/merge-patch+json"},
            )
        return name

    async def is_running(self, ref: str) -> bool:
        resp = await self._call("GET", f"/apis/batch/v1/namespaces/{self.namespace}/jobs/{ref}")
        if resp.status_code == 404:
            return False
        if resp.status_code != 200:
            raise ExecutorError(f"job read failed ({resp.status_code})")
        status = resp.json().get("status") or {}
        if status.get("succeeded") or status.get("failed"):
            return False
        finished = {c.get("type") for c in status.get("conditions") or [] if c.get("status") == "True"}
        return not finished & {"Complete", "Failed"}

    async def logs(self, ref: str, tail: int = 80) -> str:
        try:
            pods = await self._call(
                "GET", f"/api/v1/namespaces/{self.namespace}/pods", params={"labelSelector": f"{LABEL}={ref}"}
            )
            items = pods.json().get("items") or [] if pods.status_code == 200 else []
            if not items:
                return ""
            pod = items[-1]
            resp = await self._call(
                "GET",
                f"/api/v1/namespaces/{self.namespace}/pods/{pod['metadata']['name']}/log",
                params={"tailLines": tail, "container": "runner"},
            )
        except (ExecutorError, ValueError, KeyError):
            return ""
        text = resp.text if resp.status_code == 200 else ""
        for st in (pod.get("status") or {}).get("containerStatuses") or []:
            term = (st.get("state") or {}).get("terminated") or {}
            if term:
                text += f"\n[exit code {term.get('exitCode')}, {term.get('reason', '')}]"
        return text

    # ---------------------------------------------------------------- cli tools (ADR 0025)

    def tool_manifests(
        self, run_id: str, image: str, argv: list[str], env: dict[str, str], timeout_s: float
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """(Secret, Job) of one tool run: every env value (credentials) in the Secret, uid 65534, /tmp only."""
        name = f"z-tool-{run_id}"
        secret = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name, "namespace": self.namespace, "labels": {LABEL: name}},
            "type": "Opaque",
            "data": {k: base64.b64encode(v.encode()).decode() for k, v in env.items()},
        }
        container_env = [{"name": "HOME", "value": "/tmp"}] + [  # noqa: S108 - emptyDir inside the pod
            {"name": k, "valueFrom": {"secretKeyRef": {"name": name, "key": k}}} for k in sorted(env)
        ]
        pod: dict[str, Any] = {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65534,
                "runAsGroup": 65534,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "tool",
                    "image": image,
                    "imagePullPolicy": "IfNotPresent",
                    "args": list(argv),
                    "workingDir": "/tmp",  # noqa: S108
                    "env": container_env,
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "privileged": False,
                        "runAsNonRoot": True,
                        "capabilities": {"drop": ["ALL"]},
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "resources": {
                        "limits": {"cpu": "1", "memory": "512Mi", "ephemeral-storage": "256Mi"},
                        "requests": {"cpu": "100m", "memory": "64Mi"},
                    },
                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],  # noqa: S108
                }
            ],
            "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "128Mi"}}],
        }
        if self.runtime_class:
            pod["runtimeClassName"] = self.runtime_class
        labels = {LABEL: name, "app.kubernetes.io/name": "z-tool", "app.kubernetes.io/part-of": "z"}
        job = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": name, "namespace": self.namespace, "labels": labels},
            "spec": {
                "backoffLimit": 0,
                "ttlSecondsAfterFinished": 300,
                "activeDeadlineSeconds": int(timeout_s) + 10,
                "template": {"metadata": {"labels": labels}, "spec": pod},
            },
        }
        return secret, job

    async def run_tool(self, image: str, argv: list[str], env: dict[str, str], timeout_s: float) -> ToolRun:
        run_id = secrets.token_hex(8)
        name, ns = f"z-tool-{run_id}", self.namespace
        secret, job = self.tool_manifests(run_id, image, argv, env, timeout_s)
        created = await self._call("POST", f"/api/v1/namespaces/{ns}/secrets", json=secret)
        if created.status_code not in (200, 201):
            raise ExecutorError(f"tool secret create refused ({created.status_code}): {created.text[:300]}")
        try:
            started = await self._call("POST", f"/apis/batch/v1/namespaces/{ns}/jobs", json=job)
            if started.status_code not in (200, 201):
                raise ExecutorError(f"tool job create refused ({started.status_code}): {started.text[:300]}")
            deadline = time.monotonic() + timeout_s
            pod_name, exit_code, timed_out = None, None, False
            while True:
                pods = await self._call(
                    "GET", f"/api/v1/namespaces/{ns}/pods", params={"labelSelector": f"{LABEL}={name}"}
                )
                items = (pods.json().get("items") or []) if pods.status_code == 200 else []
                if items:
                    pod_name = items[-1]["metadata"]["name"]
                    for st in (items[-1].get("status") or {}).get("containerStatuses") or []:
                        term = (st.get("state") or {}).get("terminated")
                        if term:
                            exit_code = int(term.get("exitCode", -1))
                if exit_code is not None:
                    break
                if time.monotonic() > deadline:
                    timed_out = True
                    break
                await asyncio.sleep(1)
            stdout = ""
            if pod_name:
                log = await self._call(
                    "GET",
                    f"/api/v1/namespaces/{ns}/pods/{pod_name}/log",
                    params={"container": "tool", "limitBytes": MAX_TOOL_OUTPUT + 1},
                )
                stdout = log.text if log.status_code == 200 else ""
        finally:
            await self._call(
                "DELETE", f"/apis/batch/v1/namespaces/{ns}/jobs/{name}", params={"propagationPolicy": "Background"}
            )
            await self._call("DELETE", f"/api/v1/namespaces/{ns}/secrets/{name}")
        truncated = len(stdout.encode()) > MAX_TOOL_OUTPUT
        if truncated:
            stdout = stdout.encode()[:MAX_TOOL_OUTPUT].decode("utf-8", errors="ignore")
        # the pod log mixes stdout and stderr: on failure the whole log is the diagnosis
        stderr = stdout[-4000:] if exit_code not in (0, None) else ""
        return ToolRun(exit_code, stdout, stderr, timed_out, truncated)

    async def stop(self, ref: str) -> None:
        ns = self.namespace
        await self._call(
            "DELETE", f"/apis/batch/v1/namespaces/{ns}/jobs/{ref}", params={"propagationPolicy": "Background"}
        )
        await self._call("DELETE", f"/api/v1/namespaces/{ns}/secrets/{ref}")
