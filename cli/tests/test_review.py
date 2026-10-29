"""Review through `z`: preview and runs-as before approving, approve, reject, edit, destructive confirmation,
step-up, separation of duties, approver scope."""

from __future__ import annotations

import json
from typing import Any

from cli.tests.harness import ISSUE, LiveServer, Z, expose, plain, runner_for, write_mission
from tests.support.missions import RecordingExecutor


def propose(
    live: LiveServer, executor: RecordingExecutor, token: str, operation: str, payload: dict[str, Any], connector: str
) -> tuple[str, dict[str, Any]]:
    mission_id = write_mission(live, token, [operation])
    runner = runner_for(live, executor, mission_id)
    resp = runner.call(
        "POST",
        "/actions",
        json={"connector": connector, "operation": operation, "summary": "Create SEC ticket", "payload": payload},
    )
    assert resp.status_code == 201, resp.text
    runner.complete(output=None, status="succeeded")
    return mission_id, resp.json()


def test_approve_shows_preview_and_runs_as_then_approves(z: Z, live: LiveServer, executor: RecordingExecutor) -> None:
    alice, bob, carol = z.as_user("alice"), z.as_user("bob"), z.as_user("carol")
    live.fresh_step_up("alice")
    expose(live, alice.token(), "jira")
    mission_id, action = propose(live, executor, bob.token(), "jira.issues.create", ISSUE, "jira")

    listed = json.loads(carol("review", "list", "--kind", "writes", "--json").output)
    assert listed["counts"]["writes"] == 1 and listed["items"][0]["action"]["id"] == action["id"]
    table = plain(carol("review", "list").output)
    assert "jira.issues.create" in table and "1 pending" in table

    shown = plain(carol("review", "show", action["id"]).output)
    assert "Approve write" in shown and "runs as" in shown and action["runs_as"]["identity"] in shown
    assert "Patch openssl on edge-01" in shown and "project SEC" in shown and "reversible" in shown

    denied = bob("approve", action["id"], "--yes")  # operator: no actions:approve
    assert denied.exit_code == 4 and "actions:approve" in plain(denied.output)

    unconfirmed = carol("approve", action["id"])  # no terminal, no --yes: nothing is approved
    assert unconfirmed.exit_code == 2 and "--yes" in plain(unconfirmed.output)
    assert "runs as" in plain(unconfirmed.output)  # the preview was shown before asking

    approved = carol("approve", action["id"], "--yes", "--comment", "ok", "--json")
    assert approved.exit_code == 0, approved.output
    body = json.loads(approved.output)
    assert body["status"] == "approved" and body["approvals"][0]["by"] == "carol"
    again = carol("approve", action["id"], "--yes")
    assert again.exit_code == 1 and "approved" in plain(again.output)  # only pending actions can be decided
    mission = json.loads(bob("missions", "show", mission_id, "--json").output)
    assert mission["pending_actions"] == 0


def test_edit_supersedes_then_reject(z: Z, live: LiveServer, executor: RecordingExecutor) -> None:
    alice, bob, carol = z.as_user("alice"), z.as_user("bob"), z.as_user("carol")
    live.fresh_step_up("alice")
    expose(live, alice.token(), "jira")
    _, action = propose(live, executor, bob.token(), "jira.issues.create", ISSUE, "jira")

    edited = carol("review", "edit", action["id"], "--set", "summary=Patch openssl on edge-01 and edge-02", "--json")
    assert edited.exit_code == 0, edited.output
    new = json.loads(edited.output)
    assert (
        new["edited_from"] == action["id"] and new["payload"]["summary"].endswith("edge-02") and new["approvals"] == []
    )
    old = json.loads(carol("review", "show", action["id"], "--json").output)
    assert old["status"] == "superseded"
    stale = carol("approve", action["id"], "--yes")
    assert stale.exit_code == 1 and new["id"] in plain(stale.output)  # points to the new proposal

    no_reason = carol("reject", new["id"], "--reason", " ")
    assert no_reason.exit_code == 2
    rejected = carol("reject", new["id"], "--reason", "duplicate of SEC-12", "--json")
    assert rejected.exit_code == 0 and json.loads(rejected.output)["status"] == "rejected"


def test_destructive_needs_explicit_confirmation_and_step_up(
    z: Z, live: LiveServer, executor: RecordingExecutor
) -> None:
    alice, bob, carol = z.as_user("alice"), z.as_user("bob"), z.as_user("carol")
    live.fresh_step_up("alice")
    expose(live, alice.token(), "logos", {"logos.accounts.disable": True})
    _, action = propose(live, executor, bob.token(), "logos.accounts.disable", {"id": "jdoe"}, "logos")
    assert action["class"] == "destructive" and action["required_approvals"] == 2

    shown = plain(carol("review", "show", action["id"]).output)
    assert "Approve destructive action" in shown and "NOT reversible" in shown and "0/2" in shown

    yes_only = carol("approve", action["id"], "--yes")  # --yes is never enough for a destructive action
    assert yes_only.exit_code == 2 and "--confirm-destructive logos.accounts.disable" in plain(yes_only.output)
    wrong = carol("approve", action["id"], "--confirm-destructive", "logos.accounts.enable")
    assert wrong.exit_code == 2

    stale = carol("approve", action["id"], "--confirm-destructive", "logos.accounts.disable")
    assert stale.exit_code == 4  # a CLI session has no fresh step-up
    assert "step-up" in plain(stale.output) and "web app" in plain(stale.output)

    live.fresh_step_up("carol")
    first = carol("approve", action["id"], "--confirm-destructive", "logos.accounts.disable", "--json")
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["status"] == "pending"  # 1 of 2
    twice = carol("approve", action["id"], "--confirm-destructive", "logos.accounts.disable")
    assert twice.exit_code == 1 and "Separation of duties" in plain(twice.output)
    second = alice("approve", action["id"], "--confirm-destructive", "logos.accounts.disable", "--json")
    assert second.exit_code == 0 and json.loads(second.output)["status"] == "approved"


def test_four_eyes_starter_cannot_approve(z: Z, live: LiveServer, executor: RecordingExecutor) -> None:
    alice = z.as_user("alice")
    live.fresh_step_up("alice")
    expose(live, alice.token(), "jira")
    mission_id = write_mission(live, alice.token(), ["jira.issues.create"])
    live.set_four_eyes(mission_id)
    runner = runner_for(live, executor, mission_id)
    action = runner.call(
        "POST",
        "/actions",
        json={"connector": "jira", "operation": "jira.issues.create", "summary": "Create", "payload": ISSUE},
    ).json()
    shown = plain(alice("review", "show", action["id"]).output)
    assert "four-eyes" in shown
    own = alice("approve", action["id"], "--yes")
    assert own.exit_code == 1 and "four-eyes" in plain(own.output)


def test_interactive_review_needs_a_terminal(z: Z) -> None:
    carol = z.as_user("carol")
    result = carol("review")
    assert result.exit_code == 2 and "needs a terminal" in plain(result.output)
