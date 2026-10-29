"""cli connectors through the gateway (ADR 0025): the reviewed argv template, agent arguments that can never become
flags, the credential only in the tool's environment, the output redacted, and the image allowlist."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from z_server.executors import ToolRun

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import base_config, csrf, seed_packs, sign_in_as, use_jar

IMAGE = "registry.example.internal/kubectl@sha256:" + "d" * 64
KUBE = {
    "name": "kube",
    "kind": "cli",
    "cli": {"image": IMAGE, "env": {"KUBE_TOKEN": "{{auth.secret}}"}, "output": "json", "timeout_s": 30},
    "auth": {"read": {"type": "bearer", "secret": "env:KUBE_RO"}},
    "declared": [
        {
            "id": "pods.list",
            "command": ["kubectl", "get", "pods", "-n", "{{args.namespace}}", "-o", "json"],
            "input_schema": {
                "type": "object",
                "required": ["namespace"],
                "properties": {"namespace": {"type": "string"}},
            },
        }
    ],
    "data_class": "C1",
}


class FakeSandbox:
    def __init__(self) -> None:
        self.runs: list[tuple[str, list[str], dict[str, str]]] = []
        self.result = ToolRun(0, '{"items": [{"name": "web-1", "token": "ro-kube-token"}]}', "")

    async def run_tool(self, image: str, argv: list[str], env: dict[str, str], timeout_s: float) -> ToolRun:
        self.runs.append((image, argv, env))
        return self.result


@pytest.fixture
def sandbox() -> FakeSandbox:
    return FakeSandbox()


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, sandbox: FakeSandbox, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setenv("KUBE_RO", "ro-kube-token")
    config = base_config(oidc.issuer, **{"executor.tool_images": [IMAGE]})
    client = make_client(config=config, llm_keys=RecordingKeyAdmin(), sandbox=sandbox)
    seed_packs(client)
    return client


def _runner(app: TestClient, oidc: FakeGitLabOIDC) -> ScriptedRunner:
    alice = sign_in_as(app, oidc, "alice")
    bob = sign_in_as(app, oidc, "bob")
    use_jar(app, alice)
    created = app.post("/api/v1/connectors", json=KUBE, headers=csrf(app))
    assert created.status_code == 201, created.text
    ops = app.get("/api/v1/connectors/kube/operations").json()
    assert [o["id"] for o in ops] == ["kube.pods.list"] and ops[0]["class"] == "read"
    app.post(
        "/api/v1/connectors/kube/approve-exposure",
        json={"operations": [{"id": "kube.pods.list", "exposed": True}]},
        headers=csrf(app),
    )
    use_jar(app, bob)
    plan = {"steps": [{"id": "s", "agent": "orchestrator", "prompt": "Look"}]}
    mission = app.post(
        "/api/v1/missions",
        json={"title": "Pods", "prompt": "Look", "plan": plan, "connectors": ["kube"]},
        headers=csrf(app),
    )
    assert mission.status_code == 201, mission.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    return runner


def call(app: TestClient, runner: ScriptedRunner, args: dict[str, Any]) -> dict[str, Any]:
    resp = app.post(
        "/gateway/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "kube__pods__list", "arguments": args},
        },
        headers={"Authorization": f"Bearer {runner.session}", "Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["result"])


def test_a_read_runs_the_reviewed_argv_with_the_credential_in_env(
    app: TestClient, oidc: FakeGitLabOIDC, sandbox: FakeSandbox
) -> None:
    runner = _runner(app, oidc)
    result = call(app, runner, {"namespace": "payments"})
    assert result["isError"] is False, result
    [(image, argv, env)] = sandbox.runs
    assert image == IMAGE and argv == ["kubectl", "get", "pods", "-n", "payments", "-o", "json"]
    assert env["KUBE_TOKEN"] == "ro-kube-token"
    text = result["content"][0]["text"]
    assert "web-1" in text and "ro-kube-token" not in text  # the output is redacted like any tool result


def test_arguments_cannot_become_flags(app: TestClient, oidc: FakeGitLabOIDC, sandbox: FakeSandbox) -> None:
    runner = _runner(app, oidc)
    result = call(app, runner, {"namespace": "--all-namespaces"})
    assert result["isError"] is True and "cannot start with '-'" in result["content"][0]["text"]
    assert sandbox.runs == []


def test_a_failing_tool_is_an_error_with_its_stderr(
    app: TestClient, oidc: FakeGitLabOIDC, sandbox: FakeSandbox
) -> None:
    runner = _runner(app, oidc)
    sandbox.result = ToolRun(1, "", 'Error from server (Forbidden): pods is forbidden: User "z-ro"')
    result = call(app, runner, {"namespace": "kube-system"})
    assert result["isError"] is True and "exited with 1" in result["content"][0]["text"]


def test_tool_images_must_be_allowlisted(app: TestClient, oidc: FakeGitLabOIDC) -> None:
    use_jar(app, sign_in_as(app, oidc, "alice"))
    other = {**KUBE, "name": "kube2", "cli": {**KUBE["cli"], "image": "evil/tool@sha256:" + "e" * 64}}
    refused = app.post("/api/v1/connectors", json=other, headers=csrf(app))
    assert refused.status_code == 422 and "executor.tool_images" in refused.text
