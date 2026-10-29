"""End to end on the compose stack (marker e2e): z-server all -> z-socket-proxy -> runner container ->
LiteLLM -> fake LLM. Start the stack first (see compose.e2e.yml), then: uv run pytest -m e2e tests/e2e."""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.e2e
Z = os.environ.get("Z_E2E_URL", "http://127.0.0.1:8000")  # z-server (the web app is z-ui on :8080)
LLM = os.environ.get("Z_E2E_LLM_URL", "http://127.0.0.1:8090")
NOTES = b"# Release 2.4\n\nVersion 2.4 adds single sign-on. Password login is removed for staff.\n"
SUMMARY = {
    "title": "Release 2.4 adds SSO",
    "bullets": ["SSO with the corporate IdP.", "No more staff passwords."],
    "source": "notes.md",
}


def step(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"tool_calls": [{"name": name, "arguments": arguments}]}


@pytest.fixture(scope="module")
def api() -> httpx.Client:
    try:
        httpx.get(f"{Z}/readyz", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("compose stack not running")
    return httpx.Client(base_url=f"{Z}/api/v1", timeout=30)


def wait(api: httpx.Client, mission_id: str, until: set[str], timeout_s: float = 240) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        mission = api.get(f"/missions/{mission_id}").json()
        if mission["status"] in until:
            return dict(mission)
        time.sleep(1)
    raise AssertionError(f"mission still {mission['status']}")


def test_summarize_file_runs_in_a_hardened_runner(api: httpx.Client) -> None:
    httpx.post(f"{LLM}/_fake/reset").raise_for_status()
    httpx.post(
        f"{LLM}/_fake/rules",
        json={
            "match": {"has_tools": True},
            "responses": [
                step("read", {"path": "notes.md"}),
                step("edit", {"command": "write", "path": "result.json", "content": json.dumps(SUMMARY)}),
                step("finish", {"message": "summary written"}),
            ],
        },
    ).raise_for_status()
    created = api.post("/missions", json={"playbook": "summarize-file", "inputs": {"file": "notes.md"}, "start": False})
    assert created.status_code == 201, created.text
    mission_id = created.json()["id"]
    up = api.post(f"/missions/{mission_id}/files", files={"file": ("notes.md", NOTES, "text/markdown")})
    assert up.status_code == 201, up.text
    assert api.post(f"/missions/{mission_id}/start", json={}).status_code == 200

    container = None
    deadline = time.monotonic() + 60
    while container is None and time.monotonic() < deadline:
        out = subprocess.run(
            ["docker", "ps", "-a", "--filter", "label=io.z.job", "--format", "{{.ID}} {{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        jobs = api.get(f"/missions/{mission_id}/jobs").json()["items"]
        name = f"z-job-{jobs[0]['id'].lower().replace('_', '-')}" if jobs else None
        container = next((line.split()[0] for line in out.splitlines() if name and line.split()[1] == name), None)
        time.sleep(0.5)
    assert container, "no runner container was started"
    inspect = json.loads(
        subprocess.run(["docker", "inspect", container], capture_output=True, text=True, check=True).stdout
    )[0]
    host = inspect["HostConfig"]
    assert host["ReadonlyRootfs"] and host["CapDrop"] == ["ALL"] and not host["Privileged"]
    assert inspect["Config"]["User"] == "10001:10001" and host["NetworkMode"] == "z_runners"
    image_env = set(
        json.loads(
            subprocess.run(
                ["docker", "image", "inspect", inspect["Config"]["Image"]], capture_output=True, text=True, check=True
            ).stdout
        )[0]["Config"]["Env"]
    )
    added = {e.split("=", 1)[0] for e in inspect["Config"]["Env"] if e not in image_env}
    assert added == {"Z_SERVER_URL", "Z_JOB_ID", "Z_JOB_TOKEN"}, added  # no model key, no tool credential

    mission = wait(api, mission_id, {"succeeded", "failed", "waiting", "cancelled", "timed_out", "budget_exceeded"})
    jobs = api.get(f"/missions/{mission_id}/jobs").json()["items"]
    assert mission["status"] == "succeeded", (mission, jobs)
    assert jobs[0]["output"] == SUMMARY and jobs[0]["tokens_in"] > 0
    replay = api.get(f"/missions/{mission_id}/replay").json()
    types = {e["type"] for e in replay["events"]}
    assert {"tool_call", "tool_result", "usage", "file_edit"} <= types, types
    assert {a["name"] for a in replay["artifacts"]} >= {"notes.md", "result.json"}
    llm_requests = httpx.get(f"{LLM}/_fake/requests").json()
    assert llm_requests and all(r["model"] in {"fake-glm", "fake-qwen"} for r in llm_requests)
