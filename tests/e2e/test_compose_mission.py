"""End to end on the compose stack (marker e2e): z-server all -> z-socket-proxy -> runner container ->
LiteLLM -> fake LLM. Start the stack first (see compose.e2e.yml), then: uv run pytest -m e2e tests/e2e.
Without a stack the tests skip, except in CI (CI or Z_E2E_REQUIRED set), where they fail."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

pytestmark = pytest.mark.e2e
Z = os.environ.get("Z_E2E_URL", "http://127.0.0.1:8000")  # z-server (the web app is z-ui on :8080)
LLM = os.environ.get("Z_E2E_LLM_URL", "http://127.0.0.1:8090")
LITELLM_CONFIG = Path(__file__).resolve().parents[2] / "config" / "litellm.yaml"  # mounted into the litellm container


def configured_models() -> set[str]:
    """The upstream model ids of the LiteLLM config the stack runs with (`openai/<id>` -> `<id>`)."""
    data = yaml.safe_load(LITELLM_CONFIG.read_text(encoding="utf-8"))
    return {str(m["litellm_params"]["model"]).removeprefix("openai/") for m in data["model_list"]}


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
    except httpx.HTTPError as exc:
        # in CI the stack was started by the job: an unreachable z-server is a failure, not a reason to skip
        if os.environ.get("CI") or os.environ.get("Z_E2E_REQUIRED"):
            pytest.fail(f"compose stack not ready at {Z}/readyz: {exc}")
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
    added = dict(e.split("=", 1) for e in inspect["Config"]["Env"] if e not in image_env)
    proxy_vars = {"HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "NO_PROXY", "no_proxy"}
    # no model key, no tool credential: the job identity plus, when egress is configured, the egress proxy
    assert set(added) - proxy_vars == {"Z_SERVER_URL", "Z_JOB_ID", "Z_JOB_TOKEN"}, sorted(added)
    assert not any("@" in added[k] for k in proxy_vars & set(added)), "proxy URLs never carry credentials"

    mission = wait(api, mission_id, {"succeeded", "failed", "waiting", "cancelled", "timed_out", "budget_exceeded"})
    jobs = api.get(f"/missions/{mission_id}/jobs").json()["items"]
    assert mission["status"] == "succeeded", (mission, jobs)
    assert jobs[0]["output"] == SUMMARY and jobs[0]["tokens_in"] > 0
    replay = api.get(f"/missions/{mission_id}/replay").json()
    types = {e["type"] for e in replay["events"]}
    assert {"tool_call", "tool_result", "usage", "file_edit"} <= types, types
    assert {a["name"] for a in replay["artifacts"]} >= {"notes.md", "result.json"}
    llm_requests = httpx.get(f"{LLM}/_fake/requests").json()
    # every model call went through LiteLLM to the inference endpoint, with a model of the configured list
    assert llm_requests and {r["model"] for r in llm_requests} <= configured_models(), llm_requests


def test_the_inferx_key_set_from_z_reaches_the_gateway(api: httpx.Client) -> None:
    """ADR 0027 on the real LiteLLM image: the key goes through LiteLLM's credentials API and is the one LiteLLM
    sends upstream from the next call; Z answers with its fingerprint only."""
    key = "ixk_" + "e2e0ab12" * 8
    httpx.delete(f"{LLM}/_fake/requests", timeout=10).raise_for_status()
    saved = api.put("/inference/credential", json={"api_key": key})
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["source"] == "ui" and out["check"]["ok"] is True, out
    assert key not in saved.text and len(out["fingerprint"]) == 12
    sent = [r["headers"].get("authorization", "") for r in httpx.get(f"{LLM}/_fake/requests", timeout=10).json()]
    assert sent and all(h == f"Bearer {key}" for h in sent), "the check did not go out with the new key"
    assert api.get("/inference/credential").json()["source"] == "ui"
