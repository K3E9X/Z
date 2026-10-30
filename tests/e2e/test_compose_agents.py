"""An agent renamed "Atlas" on the compose stack: `@atlas` starts a mission from the API (what the web app sends)
and from the `z` CLI, and the runner works with that agent's own skills and tools."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.e2e import stack
from tests.e2e.stack import finish

pytestmark = pytest.mark.e2e

AGENT = "summarizer"  # packs/demo: skill `summarize`, tools [read]


@pytest.fixture
def atlas(api: httpx.Client) -> Iterator[dict[str, Any]]:
    before = api.get(f"/agents/{AGENT}").json()
    renamed = api.patch(
        f"/agents/{AGENT}",
        json={"display_name": "Atlas", "handle": "atlas", "avatar": {"initials": "AT", "color": "#6AA6FF"}},
    )
    assert renamed.status_code == 200, renamed.text
    yield dict(renamed.json())
    restored = api.patch(
        "/agents/atlas",
        json={"display_name": before["display_name"], "handle": before["handle"], "avatar": before["avatar"]},
    )
    assert restored.status_code == 200, restored.text


def own_tools_and_skills(request: dict[str, Any], profile: dict[str, Any]) -> None:
    """The model call of a job of `profile`: its role, its skills, its tools and nothing else."""
    body = request["body"]
    system = json.dumps(body["messages"][0]["content"])
    assert profile["role"] in system, system[:400]
    assert all(f"Skill: {s}" in system for s in profile["skills"]), system[:2000]
    names = {t["function"]["name"] for t in body["tools"]}
    assert "read" in names and not any("__" in n or n in {"terminal", "propose_action"} for n in names), names


def test_renamed_agent_is_reachable_as_atlas(
    api: httpx.Client, llm: stack.FakeLLM, atlas: dict[str, Any], tmp_path: Path
) -> None:
    assert atlas["name"] == AGENT and atlas["handle"] == "atlas" and atlas["display_name"] == "Atlas"
    assert api.get("/agents/@atlas").json()["name"] == AGENT
    renamed = api.get("/audit", params={"action": "agent.renamed", "target_id": atlas["id"]}).json()["items"]
    assert renamed and renamed[0]["after"]["handle"] == "atlas", renamed
    llm.rules(
        {
            "match": {"contains": r"What is new in release 2\.4"},
            "responses": [finish("Release 2.4 adds SSO.")],
            "repeat": True,
        }
    )

    # the web app's New mission: a prompt that mentions @atlas
    created = api.post("/missions", json={"prompt": "@atlas What is new in release 2.4?", "agents": ["@atlas"]})
    assert created.status_code == 201, created.text
    mission_id = created.json()["id"]
    done = stack.wait_status(api, mission_id, stack.TERMINAL, timeout_s=180)
    [job] = stack.jobs(api, mission_id)
    assert done["status"] == "succeeded", (done, job)
    assert job["agent"] == AGENT and job["agent_version"] == atlas["version"]
    [request] = llm.requests()
    own_tools_and_skills(request, atlas)

    # the CLI, headless: `z -p "@atlas …"`
    z = stack.z_cli(str(tmp_path))
    run = z("-p", "@atlas What is new in release 2.4?", "--output-format", "json", timeout=240)
    assert run.returncode == 0, run.stdout + run.stderr
    final = json.loads(run.stdout)
    assert final["status"] == "succeeded" and final["plan"]["steps"][0]["agent"] == AGENT, final
    [cli_job] = stack.jobs(api, final["id"])
    assert cli_job["agent"] == AGENT
    own_tools_and_skills(llm.requests()[-1], atlas)
