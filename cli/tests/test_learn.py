"""z eval, z learn, z objectives, z assign, z agents create/rename/duplicate against the real server."""

from __future__ import annotations

import json

from cli.tests.harness import LiveServer, Z, plain


def test_objectives_assign_and_agents(z: Z, live: LiveServer) -> None:
    erin = z.as_user("erin")  # builder
    added = erin(
        "objectives",
        "add",
        "--agent",
        "@vuln-analyst",
        "--title",
        "Findings triaged",
        "--metric",
        "untriaged_findings",
        "--json",
    )
    assert added.exit_code == 0, added.output
    objective = json.loads(added.output)
    listed = erin("objectives", "list", "--agent", "@vuln-analyst")
    assert listed.exit_code == 0 and "Findings triaged" in plain(listed.output)
    checked = erin("objectives", "check", objective["id"])
    assert checked.exit_code == 0 and "on_track" in plain(checked.output)
    shown = erin("objectives", "show", objective["id"])
    assert shown.exit_code == 0 and "untriaged_findings is 0" in plain(shown.output)
    paused = erin("objectives", "pause", objective["id"])
    assert paused.exit_code == 0 and "paused" in plain(paused.output)
    assert "on_track" in plain(erin("objectives", "pause", objective["id"], "--resume").output)

    assigned = erin("assign", "@vuln-analyst", "jira_project:SECOPS", "group:infra/")
    assert assigned.exit_code == 0 and "jira_project:SECOPS" in plain(assigned.output), assigned.output
    assert erin("assign", "@vuln-analyst", "nonsense").exit_code == 2

    copy = erin("agents", "duplicate", "@ticket-manager", "--name", "Atlas")
    assert copy.exit_code == 0 and "@atlas" in plain(copy.output), copy.output
    renamed = erin("agents", "rename", "@atlas", "--name", "Vega")
    assert renamed.exit_code == 0 and "@vega" in plain(renamed.output), renamed.output
    templated = erin("agents", "create", "--from-template", "@vuln-analyst", "--name", "Orion")
    assert templated.exit_code == 0 and "@orion" in plain(templated.output)
    assert erin("agents", "create").exit_code == 2


def test_learning_commands(z: Z, live: LiveServer) -> None:
    alice = z.as_user("alice")
    assert alice("learn", "proposals").exit_code == 0
    assert alice("learn", "signals", "--agent", "@summarizer").exit_code == 0
    assert alice("learn", "show", "prp_nope").exit_code == 1
    started = alice("eval", "@summarizer", "--cases", "sum-1", "--no-wait", "--json")
    assert started.exit_code == 0, started.output
    [run] = json.loads(started.output)
    assert run["target"] == "agent:summarizer" and run["status"] == "running" and len(run["cases"]) == 1
    bob = z.as_user("bob")
    denied = bob("eval", "@summarizer", "--no-wait")
    assert denied.exit_code == 4 and "learning:apply" in plain(denied.output)
