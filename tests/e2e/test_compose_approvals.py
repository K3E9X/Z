"""A destructive action on the compose stack (invariant 4): logos.accounts.disable ships unexposed, an admin enables
it, an agent proposes it, and one approval does not apply it. A second, different approver is needed; then the
action executor disables the account once, with the write account."""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx
import pytest

from tests.e2e import stack
from tests.e2e.stack import finish, step

pytestmark = pytest.mark.e2e

DISABLE = "logos.accounts.disable"


@pytest.fixture
def disable_enabled(api: httpx.Client, reviewed: None) -> Iterator[None]:
    [op] = [o for o in api.get("/connectors/logos/operations").json() if o["id"] == DISABLE]
    assert op["class"] == "destructive" and op["exposed"] is False, op  # pack exposure never covers it
    enabled = api.patch(f"/operations/{DISABLE}", json={"exposed": True})
    assert enabled.status_code == 200 and enabled.json()["exposed"] is True, enabled.text
    yield
    assert api.patch(f"/operations/{DISABLE}", json={"exposed": False}).status_code == 200


def test_destructive_action_needs_two_distinct_approvers(
    api: httpx.Client, llm: stack.FakeLLM, tools: stack.FakeTools, disable_enabled: None
) -> None:
    llm.rules(
        {
            "match": {"contains": r"List accounts inactive for 90 days"},
            "responses": [
                step("logos__accounts__list", {"inactive_days": 90}),
                step(
                    "propose_action",
                    {"operation": DISABLE, "payload": {"id": "acc-004"}, "summary": "Disable acc-004 (leaver)"},
                ),
                finish("Proposed one deactivation."),
            ],
        }
    )
    operator = stack.sign_in_local("tester")  # team Cyber Ops: starts missions, cannot approve
    approver = stack.sign_in_local("tester-approver")  # approver of Cyber Ops
    created = operator.post("/missions", json={"playbook": "inactive-account-review", "inputs": {"inactive_days": 90}})
    assert created.status_code == 201, created.text
    mission_id = created.json()["id"]
    waiting = stack.wait_status(api, mission_id, {"waiting"} | stack.TERMINAL, timeout_s=180)
    assert waiting["status"] == "waiting" and waiting["waiting_reason"] == "approval", waiting
    [item] = [
        i for i in approver.get("/review", params={"mission": mission_id}).json()["items"] if i["kind"] == "action"
    ]
    action = item["action"]
    assert action["operation"] == DISABLE and action["payload"] == {"id": "acc-004"}
    assert action["class"] == "destructive" and action["required_approvals"] == 2 and action["reversible"] is False

    refused = operator.post(f"/actions/{action['id']}/approve", json={})
    assert refused.status_code == 403, refused.text

    first = approver.post(f"/actions/{action['id']}/approve", json={"comment": "leaver"})
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "pending" and [a["by"] for a in first.json()["approvals"]] == [
        "local:tester-approver"
    ]
    twice = approver.post(f"/actions/{action['id']}/approve", json={})
    assert twice.status_code == 403 and twice.json()["type"] == "urn:z:problem:separation-of-duties", twice.text
    batch = api.post("/actions:batch-approve", json={"action_ids": [action["id"]]})
    assert batch.status_code == 200 and batch.json()["results"][0]["ok"] is False, batch.text  # one by one only

    time.sleep(6)  # several executor ticks (every 2 s): one approval is not enough to apply it
    assert api.get(f"/actions/{action['id']}").json()["status"] == "pending"
    assert stack.mission(api, mission_id)["status"] == "waiting"
    assert tools.writes(DISABLE) == []

    second = api.post(f"/actions/{action['id']}/approve", json={})  # the dev admin: a different approver
    assert second.status_code == 200 and second.json()["status"] == "approved", second.text
    done = stack.wait_status(api, mission_id, stack.TERMINAL, timeout_s=120)
    assert done["status"] == "succeeded", done
    applied = api.get(f"/actions/{action['id']}").json()
    assert applied["status"] == "applied" and [a["by"] for a in applied["approvals"]] == [
        "local:tester-approver",
        "dev",
    ]
    [write] = tools.writes(DISABLE)
    assert write["token_kind"] == "write" and "acc-004" in str(write["data"])
