"""Kubernetes executor against a small in-memory Kubernetes API (httpx MockTransport)."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx
import pytest
from z_server.executors import ExecutorError
from z_server.executors.kubernetes import KubernetesExecutor

ENV = {"Z_JOB_ID": "job_01ABC", "Z_JOB_TOKEN": "zj_secret-token", "Z_SERVER_URL": "http://z-server.z.svc:8000/api/v1"}


class FakeKube:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.refuse_jobs = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        assert request.headers["authorization"] == "Bearer sa-token"
        if method == "POST":
            body = json.loads(request.content)
            if self.refuse_jobs and body["kind"] == "Job":
                return httpx.Response(403, json={"message": "forbidden"})
            key = f"{path}/{body['metadata']['name']}"
            if key in self.objects:
                return httpx.Response(409, json={"reason": "AlreadyExists"})
            body["metadata"]["uid"] = f"uid-{len(self.objects)}"
            self.objects[key] = body
            return httpx.Response(201, json=body)
        if method == "PATCH":
            self.objects[path]["metadata"].update(json.loads(request.content)["metadata"])
            return httpx.Response(200, json=self.objects[path])
        if method == "DELETE":
            return httpx.Response(200 if self.objects.pop(path, None) else 404, json={})
        if path.endswith("/pods"):
            selector = request.url.params["labelSelector"]
            pods = (
                [
                    {
                        "metadata": {"name": "p1"},
                        "status": {
                            "containerStatuses": [{"state": {"terminated": {"exitCode": 3, "reason": "Error"}}}]
                        },
                    }
                ]
                if selector.endswith("z-job-job-01abc")
                else []
            )
            return httpx.Response(200, json={"items": pods})
        if path.endswith("/log"):
            return httpx.Response(200, text="runner: spec fetch failed")
        obj = self.objects.get(path)
        return httpx.Response(200, json=obj) if obj else httpx.Response(404, json={})


def executor(fake: FakeKube, **kw: Any) -> KubernetesExecutor:
    return KubernetesExecutor(
        image="registry.example/z-runner@sha256:" + "a" * 64,
        api_url="https://kube.test",
        token="sa-token",
        transport=httpx.MockTransport(fake),
        **kw,
    )


def test_manifest_is_restricted_and_keeps_the_token_out_of_the_pod() -> None:
    ex = executor(FakeKube(), runtime_class="gvisor")
    job = ex.job_manifest("job_01ABC", ENV)
    assert job["metadata"]["name"] == "z-job-job-01abc" and job["spec"]["backoffLimit"] == 0
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False and pod["runtimeClassName"] == "gvisor"
    assert (
        pod["securityContext"]["runAsNonRoot"] and pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    )
    container = pod["containers"][0]
    sc = container["securityContext"]
    assert sc["readOnlyRootFilesystem"] and not sc["allowPrivilegeEscalation"] and sc["capabilities"]["drop"] == ["ALL"]
    assert "zj_secret-token" not in json.dumps(job)
    [ref] = [e for e in container["env"] if e["name"] == "Z_JOB_TOKEN"]
    assert ref["valueFrom"]["secretKeyRef"] == {"name": "z-job-job-01abc", "key": "Z_JOB_TOKEN"}
    secret = ex.secret_manifest("job_01ABC", ENV)
    assert base64.b64decode(secret["data"]["Z_JOB_TOKEN"]).decode() == "zj_secret-token" and len(secret["data"]) == 1


def test_launch_status_logs_and_stop() -> None:
    fake = FakeKube()
    ex = executor(fake)

    async def go() -> None:
        ref = await ex.launch("job_01ABC", ENV)
        secret = fake.objects["/api/v1/namespaces/z-runners/secrets/z-job-job-01abc"]
        assert secret["metadata"]["ownerReferences"][0]["kind"] == "Job"  # garbage-collected with the Job
        assert await ex.is_running(ref) is True
        fake.objects[f"/apis/batch/v1/namespaces/z-runners/jobs/{ref}"]["status"] = {"failed": 1}
        assert await ex.is_running(ref) is False
        logs = await ex.logs(ref)
        assert "spec fetch failed" in logs and "exit code 3" in logs
        await ex.stop(ref)
        assert await ex.is_running(ref) is False and fake.objects == {}
        await ex.stop(ref)  # idempotent

    asyncio.run(go())


def test_refused_job_cleans_up_its_secret() -> None:
    fake = FakeKube()
    fake.refuse_jobs = True

    async def go() -> None:
        with pytest.raises(ExecutorError, match="job create refused"):
            await executor(fake).launch("job_01ABC", ENV)

    asyncio.run(go())
    assert fake.objects == {}
