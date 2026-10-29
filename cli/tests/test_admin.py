"""z admin, z audit, the catalog and connectors, config profiles and completions."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from cli.tests.harness import LiveServer, Z, plain


def test_admin_users_roles_and_step_up(z: Z, live: LiveServer) -> None:
    alice = z.as_user("alice")
    z.as_user("bob")
    users = json.loads(alice("admin", "users", "list", "--json").output)
    assert {"alice", "bob"} <= {u["username"] for u in users}
    admins = json.loads(alice("admin", "users", "list", "--role", "admin", "--json").output)
    assert [u["username"] for u in admins] == ["alice"]
    shown = json.loads(alice("admin", "users", "show", "bob", "--json").output)
    assert shown["username"] == "bob" and shown["sessions"]
    assert "operator" in plain(alice("admin", "users", "show", "bob").output)

    # admin writes need a fresh step-up; a CLI session has none
    stale = alice("admin", "roles", "grant", "bob", "approver", "--team", "Cyber Ops", "--expires", "30d", "--yes")
    assert stale.exit_code == 4 and "step-up" in plain(stale.output)

    live.fresh_step_up("alice")
    granted = alice(
        "admin", "roles", "grant", "bob", "approver", "--team", "Cyber Ops", "--expires", "30d", "--yes", "--json"
    )
    assert granted.exit_code == 0, granted.output
    binding = json.loads(granted.output)
    assert binding["role"] == "approver" and binding["scope"].startswith("team:") and binding["source"] == "manual"
    bindings = json.loads(alice("admin", "roles", "bindings", "--user", "bob", "--json").output)
    assert [b["id"] for b in bindings] == [binding["id"]]
    who = json.loads(alice("admin", "roles", "who-can", "actions:approve", "--json").output)
    assert "bob" in {u["user"] for u in who["users"]}
    own = alice("admin", "roles", "grant", "alice", "auditor", "--yes")
    assert own.exit_code == 1 and "Separation of duties" in plain(own.output)
    revoked = alice("admin", "roles", "revoke", "bob", "approver", "--team", "Cyber Ops", "--yes", "--json")
    assert revoked.exit_code == 0 and json.loads(revoked.output)["revoked"] == [binding["id"]]
    assert alice("admin", "roles", "revoke", "bob", "approver", "--team", "Cyber Ops", "--yes").exit_code == 2

    roles = json.loads(alice("admin", "roles", "list", "--json").output)
    assert {"admin", "operator", "approver"} <= {r["name"] for r in roles}
    perms = json.loads(alice("admin", "roles", "permissions", "--json").output)
    assert any(p["permission"] == "missions:start" for p in perms)
    teams = json.loads(alice("admin", "teams", "list", "--json").output)
    assert "Cyber Ops" in {t["name"] for t in teams}

    # sessions and tokens
    sessions = json.loads(alice("admin", "sessions", "list", "--user", "bob", "--json").output)
    assert sessions and all(s["user"] == "bob" for s in sessions)
    bob_session = sessions[0]["id"]
    assert alice("admin", "sessions", "revoke", bob_session, "--yes").exit_code == 0
    tokens = json.loads(alice("admin", "tokens", "list", "--json").output)
    assert isinstance(tokens, list)

    # suspension
    suspended = alice("admin", "users", "suspend", "bob", "--reason", "left", "--yes", "--json")
    assert suspended.exit_code == 0 and json.loads(suspended.output)["status"] == "suspended"
    back = alice("admin", "users", "reactivate", "bob", "--yes", "--json")
    assert json.loads(back.output)["status"] == "active"

    # a non-admin is refused with the auth exit code
    carol = z.as_user("carol")
    refused = carol("admin", "users", "list")
    assert refused.exit_code == 4 and "users:manage" in plain(refused.output)


def test_group_mapping_dry_run_and_test(z: Z, live: LiveServer, tmp_path: Path) -> None:
    alice = z.as_user("alice")
    z.as_user("bob")
    mapping = json.loads(alice("admin", "groups", "list", "--json").output)
    assert {"allowed_groups", "no_match", "group_mapping", "team_mapping"} <= set(mapping)
    assert "cyber/z/z-admins" in plain(alice("admin", "mapping", "list").output)

    proposed = dict(mapping)
    proposed["group_mapping"] = [g for g in mapping["group_mapping"] if g["group"] != "cyber/z/z-operators"]
    path = tmp_path / "mapping.yaml"
    path.write_text(yaml.safe_dump({"auth": proposed}))
    dry = json.loads(alice("admin", "groups", "dry-run", str(path), "--json").output)
    assert any(c["user"] == "bob" and c["change"] == "loss" and c["role"] == "operator" for c in dry["changes"])
    test = json.loads(alice("admin", "groups", "test", "--user", "bob", "--file", str(path), "--json").output)
    assert test["user"] == "bob" and any(c["change"] == "loss" and c["role"] == "operator" for c in test["changes"])
    text = plain(alice("admin", "groups", "test", "--user", "bob", "--file", str(path)).output)
    assert "- bob" in text and "operator" in text

    stale = alice("admin", "groups", "apply", str(path), "--yes")
    assert stale.exit_code == 4  # step-up
    live.fresh_step_up("alice")
    saved = alice("admin", "groups", "apply", str(path), "--yes", "--json")
    assert saved.exit_code == 0, saved.output


def test_audit_list_verify_export(z: Z, live: LiveServer, tmp_path: Path) -> None:
    alice = z.as_user("alice")
    bob = z.as_user("bob")
    assert bob("tokens", "create", "ci", "-s", "missions:read").exit_code == 0
    mine = json.loads(bob("audit", "list", "--json").output)
    assert mine and {e["actor"] for e in mine} == {"bob"}  # audit:read_own
    issued = json.loads(alice("admin", "audit", "search", "--action", "auth.cli_token_issued", "--json").output)
    assert len(issued) >= 2 and all(e["target_type"] == "session" for e in issued)
    recent = json.loads(alice("audit", "list", "--since", "1h", "--actor", "bob", "--json").output)
    assert recent and all(e["actor"] == "bob" for e in recent)

    verified = alice("audit", "verify", "--json")
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["ok"] is True
    assert "Audit chain intact" in plain(alice("admin", "audit", "verify").output)

    assert alice("audit", "export", "--since", "1d").exit_code == 4  # export needs a fresh step-up
    live.fresh_step_up("alice")
    out = tmp_path / "audit.jsonl"
    exported = alice("audit", "export", "--since", "1d", "-o", str(out))
    assert exported.exit_code == 0, exported.output
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines and all("hash" in e and "prev_hash" in e for e in lines)
    stdout = alice("audit", "export", "--format", "csv")
    assert stdout.output.splitlines()[0].startswith("seq,")


def test_catalog_and_connectors(z: Z, live: LiveServer) -> None:
    alice = z.as_user("alice")
    bob = z.as_user("bob")
    agents = json.loads(bob("agents", "list", "--json").output)
    assert "summarizer" in {a["handle"] for a in agents}
    agent = json.loads(bob("agents", "show", "@summarizer", "--json").output)
    assert agent["name"] == "summarizer" and agent["skills"] == ["summarize"]
    assert "Summarizer" in plain(bob("agents", "show", "summarizer").output)
    assert json.loads(bob("agents", "versions", "@summarizer", "--json").output)[0]["version"] == agent["version"]

    skills = json.loads(bob("skills", "list", "--json").output)
    assert "summarize" in {s["name"] for s in skills}
    skill = json.loads(bob("skills", "show", "summarize", "--json").output)
    assert skill["content"]

    playbooks = json.loads(bob("playbooks", "list", "--json").output)
    assert "weekly-vuln-triage" in {p["name"] for p in playbooks}
    pb = plain(bob("playbooks", "show", "summarize-file").output)
    assert "file (required)" in pb and "z run summarize-file --input file=" in pb

    connectors = json.loads(bob("connectors", "list", "--json").output)
    jira = next(c for c in connectors if c["name"] == "jira")
    assert jira["review_status"] == "pending_review"
    ops = json.loads(bob("connectors", "operations", "jira", "--json").output)
    assert any(o["id"] == "jira.issues.create" and o["class"] == "write" for o in ops)
    assert "jira.issues.create" in plain(bob("connectors", "operations", "jira").output)

    refused = bob("connectors", "approve-exposure", "jira", "--as-proposed", "--yes")
    assert refused.exit_code == 4  # connectors:manage
    live.fresh_step_up("alice")
    done = alice("connectors", "approve-exposure", "jira", "--as-proposed", "--yes", "--json")
    assert done.exit_code == 0, done.output
    assert json.loads(done.output)["review_status"] == "approved"
    hidden = alice("connectors", "set-operation", "jira.issues.create", "--hide", "--json")
    assert hidden.exit_code == 0 and json.loads(hidden.output)["exposed"] is False
    lowered = alice("connectors", "set-operation", "jira.issues.create", "--class", "read")
    assert lowered.exit_code != 0  # rules can only raise an operation's class


def test_config_profiles_and_completions(tmp_path: Path) -> None:
    cli = Z("", tmp_path)
    env = {"Z_URL": None}
    assert cli("config", "set", "url", "https://z.example/api/v1/", env=env).exit_code == 0
    assert cli("config", "get", "url", env=env).output.strip() == "https://z.example"
    assert cli("config", "set", "theme", "neon", env=env).exit_code == 2
    assert cli("config", "set", "theme", "slate", env=env).exit_code == 0
    assert cli("config", "add", "staging", "https://z-staging.example", "--use", env=env).exit_code == 0
    assert cli("config", "get", "url", env=env).output.strip() == "https://z-staging.example"
    assert cli("config", "get", "url", "--profile", "default", env=env).output.strip() == "https://z.example"
    data = json.loads(cli("config", "list", "--json", env=env).output)
    assert data["active"] == "staging" and data["profiles"]["default"]["theme"] == "slate"
    text = (tmp_path / "config.toml").read_text()
    assert 'profile = "staging"' in text and "[profiles.staging]" in text
    assert cli("config", "use", "default", env=env).exit_code == 0
    assert cli("config", "remove", "staging", env=env).exit_code == 0
    assert cli("--profile", "nope", "whoami", env=env).exit_code == 2

    for shell in ("bash", "zsh", "fish", "powershell"):
        script = cli("completions", shell, env=env)
        assert script.exit_code == 0 and "_Z_COMPLETE" in script.output
    assert "_ZCTL_COMPLETE" in cli("completions", "bash", "--prog", "zctl", env=env).output
    assert cli("--version", env=env).output.startswith("z 0.")


def test_admin_inference_key_reads_the_key_hidden_never_as_an_argument(z: Z, live: LiveServer) -> None:
    alice = z.as_user("alice")
    key = "ixk_" + "0123456789abcdef" * 4
    status = alice("admin", "inference-key", "status")
    assert status.exit_code == 0 and "no key" in plain(status.output)
    refused = alice("admin", "inference-key", "set", input="sk-not-inferx\n")
    assert refused.exit_code == 2 and "ixk_" in plain(refused.output)
    live.fresh_step_up("alice")
    saved = alice("admin", "inference-key", "set", "--json", input=key + "\n")
    assert saved.exit_code == 0, saved.output
    out = json.loads(saved.output)
    assert out["source"] == "ui" and len(out["fingerprint"]) == 12 and key not in saved.output
    shown = plain(alice("admin", "inference-key", "status").output)
    assert out["fingerprint"] in shown and key not in shown
