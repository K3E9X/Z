"""weekly-vuln-triage on the compose stack: a fan-out over 20 fake findings in real runner containers, never more
running at once than the step's concurrency, one ticket proposed, one approval, exactly one Jira issue created by
the action executor with the write account, and still one after the executor retries the action."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tests.e2e import stack
from tests.e2e.stack import finish, step

pytestmark = pytest.mark.e2e

FINDINGS = [str(i) for i in range(1001, 1021)]  # the fake DefectDojo product infra-boutiques
CONCURRENCY = 8  # fanout.concurrency of packs/vuln/playbooks/weekly-vuln-triage.yaml (qualify)
TICKET = {
    "project": "SECOPS",
    "summary": "Payments Platform: patch log4j (CVE-2021-44228) on pay-api-01, pay-api-02 and pay-worker-01",
    "description": "Critical findings 1001-1003, exploitable in context. Upgrade log4j-core to 2.17.1.",
    "labels": ["vuln", "triage"],
    "priority": "Highest",
}


def triage(finding: str) -> dict[str, Any]:
    return {
        "finding_id": finding,
        "title": f"Finding {finding}",
        "cve": None,
        "asset": "pay-api-01",
        "priority": "high",
        "exploitable_in_context": finding in {"1001", "1002", "1003"},
        "owner_team": "Payments Platform",
        "evidence": [f"DefectDojo finding {finding}"],
    }


def max_overlap(intervals: list[tuple[str, str]]) -> int:
    """Most jobs running at the same time, from their started/ended timestamps (an end before a start at a tie)."""
    points = sorted([(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals])
    peak = running = 0
    for _, delta in points:
        running += delta
        peak = max(peak, running)
    return peak


def test_weekly_triage_fans_out_within_the_cap_and_creates_one_ticket(
    api: httpx.Client, llm: stack.FakeLLM, tools: stack.FakeTools, reviewed: None
) -> None:
    llm.rules(
        {
            "match": {"contains": r"List open findings for infra-boutiques"},
            "responses": [
                step(
                    "defectdojo__findings__list",
                    {"product_name": "infra-boutiques", "severity": "Critical,High,Medium", "limit": 50},
                ),
                finish({"findings": [{"id": f, "severity": "High"} for f in FINDINGS]}),
            ],
        },
        *(
            {
                "match": {"contains": rf"Apply triage-finding to finding {f}\b"},
                "responses": [finish(triage(f))],
                "repeat": True,
            }
            for f in FINDINGS
        ),
        {
            "match": {"contains": r"Group the triage results by owning team"},
            "responses": [
                step("jira__issues__search", {"jql": "project = SECOPS AND statusCategory != Done", "maxResults": 20}),
                step(
                    "propose_action",
                    {"operation": "jira.issues.create", "payload": TICKET, "summary": "One ticket for Payments"},
                ),
                finish("Proposed one ticket for Payments Platform."),
            ],
        },
    )
    created = api.post("/missions", json={"playbook": "weekly-vuln-triage", "inputs": {"product": "infra-boutiques"}})
    assert created.status_code == 201, created.text
    mission_id = created.json()["id"]

    def capped() -> dict[str, Any] | None:
        running = [j for j in stack.jobs(api, mission_id) if j["status"] == "running" and j["step_id"] == "qualify"]
        assert len(running) <= CONCURRENCY, running  # also sampled live, not only from the timestamps below
        current = stack.mission(api, mission_id)
        return current if current["status"] in stack.TERMINAL | {"waiting"} else None

    waiting = stack.wait_for("the ticket proposal", capped, timeout_s=420, every_s=0.5)
    all_jobs = stack.jobs(api, mission_id)
    assert waiting["status"] == "waiting" and waiting["waiting_reason"] == "approval", (waiting, all_jobs)

    qualify = [j for j in all_jobs if j["step_id"] == "qualify"]
    assert sorted(j["input"]["item"]["id"] for j in qualify) == FINDINGS, [j["input"] for j in qualify]
    assert all(j["status"] == "succeeded" for j in qualify), qualify
    assert sorted(j["output"]["finding_id"] for j in qualify) == FINDINGS
    # the scheduler starts at most `concurrency` qualify jobs at once, and does start that many
    peak = max_overlap([(j["started_at"], j["ended_at"]) for j in qualify])
    assert peak == CONCURRENCY, peak

    [pull] = [j for j in all_jobs if j["step_id"] == "pull"]
    gateway_calls = [e["payload"] for e in stack.events(api, mission_id, "tool_call") if e["job_id"] == pull["id"]]
    assert [c["operation"] for c in gateway_calls] == ["defectdojo.findings.list"]

    review = api.get("/review", params={"mission": mission_id}).json()
    [item] = [i for i in review["items"] if i["kind"] == "action"]
    action = item["action"]
    assert action["operation"] == "jira.issues.create" and action["status"] == "pending"
    assert action["class"] == "write" and action["required_approvals"] == 1
    assert tools.writes("jira.issues.create") == []  # nothing is written before a human approves

    approved = api.post(f"/actions/{action['id']}/approve", json={"comment": "ok"})
    assert approved.status_code == 200 and approved.json()["status"] == "approved", approved.text
    done = stack.wait_status(api, mission_id, stack.TERMINAL, timeout_s=120)
    assert done["status"] == "succeeded", done
    applied = api.get(f"/actions/{action['id']}").json()
    assert applied["status"] == "applied" and applied["result"]["key"].startswith("SECOPS-"), applied
    [write] = tools.writes("jira.issues.create")
    assert write["token_kind"] == "write"  # the executor's account, never the gateway's read account
    assert f"z-{action['id'].lower()}" in write["data"]["fields"]["labels"]

    # a second approval changes nothing
    again = api.post(f"/actions/{action['id']}/approve", json={})
    assert again.status_code == 409, again.text

    # the executor retries an action it lost track of (crash between the Jira write and its bookkeeping):
    # it finds the issue by its label and does not create a second one
    stack.psql_owner(
        "UPDATE proposed_actions SET status = 'applying', updated_at = now() - interval '1 hour' "
        f"WHERE id = '{action['id']}'"
    )

    def recovered() -> dict[str, Any] | None:
        current = dict(api.get(f"/actions/{action['id']}").json())
        return current if current["status"] == "applied" else None

    again_applied = stack.wait_for("the executor to recover the action", recovered, timeout_s=60)
    assert again_applied["result"]["recovered"] is True and again_applied["result"]["key"] == applied["result"]["key"]
    assert len(tools.writes("jira.issues.create")) == 1
