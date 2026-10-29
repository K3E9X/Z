"""z eval, z learn, z objectives, z assign, z agents create/rename/duplicate against the real server."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

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


def test_connect_a_personal_account(z: Z, live: LiveServer, monkeypatch: Any) -> None:
    import httpx

    from tests.fakes.fake_tools import FakeTools

    with FakeTools() as tools:
        base = tools.url_for("m365")
        monkeypatch.setenv("MAIL_READ_SECRET", "read-secret")
        monkeypatch.setenv("MAIL_WRITE_SECRET", "write-secret")
        manifest = {
            "name": "mail",
            "kind": "rest",
            "display_name": "Mail",
            "base_url": f"{base}/v1.0",
            "oauth": {
                "authorize_url": f"{base}/oauth2/authorize",
                "token_url": f"{base}/oauth2/token",
                "read_app": {"client_id": "read-client", "secret": "env:MAIL_READ_SECRET"},
                "write_app": {"client_id": "write-client", "secret": "env:MAIL_WRITE_SECRET"},
            },
            "auth": {
                "read": {"user": {"flow": "authorization_code_pkce", "app": "read_app", "scopes": ["Mail.Read"]}},
                "write": {"user": {"flow": "authorization_code_pkce", "app": "write_app", "scopes": ["Mail.Send"]}},
            },
            "declared": [
                {
                    "id": "messages.list",
                    "description": "Mail",
                    "method": "GET",
                    "path": "/me/messages",
                    "input_schema": {"type": "object"},
                }
            ],
            "data_class": "C2",
        }
        alice = z.as_user("alice")
        live.fresh_step_up("alice")
        admin = live.http(alice.token())
        assert admin.post("/api/v1/connectors", json=manifest).status_code == 201
        assert (
            admin.post(
                "/api/v1/connectors/mail/approve-exposure",
                json={"operations": [{"id": "mail.messages.list", "exposed": True}]},
            ).status_code
            == 200
        )

        # the person's browser is signed in to Z (the callback only completes in the starter's own session)
        assert z.oidc is not None
        z.oidc.add_client("z-web", "z-web-secret", ["http://127.0.0.1/api/v1/auth/callback"])  # the live public_url
        z.oidc.set_current_user("bob")
        jar: dict[str, str] = {}

        def browse(url: str) -> httpx.Response:  # __Host- cookies are Secure: sent by hand over http
            cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
            resp = httpx.get(url, headers={"Cookie": cookie} if cookie else {}, follow_redirects=False)
            jar.update(dict(resp.cookies))
            return resp

        start = browse(f"{live.url}/api/v1/auth/login")
        assert start.status_code == 302, (start.status_code, start.text[:300])
        given = httpx.get(start.headers["location"], follow_redirects=False)
        assert given.status_code in (302, 303), (given.status_code, given.text[:300])
        back = urlparse(httpx.get(start.headers["location"], follow_redirects=False).headers["location"])
        signed_in = browse(f"{live.url}{back.path}?{back.query}")
        assert not signed_in.headers["location"].startswith("/signin"), signed_in.headers["location"]

        def consent(url: str, *args: Any, **kwargs: Any) -> bool:  # what the person does in the browser
            back = urlparse(httpx.get(url, follow_redirects=False).headers["location"])
            done = browse(f"{live.url}{back.path}?{back.query}")  # public_url -> test server
            assert "connected=" in done.headers["location"], done.headers["location"]
            return True

        monkeypatch.setattr("webbrowser.open", consent)
        bob = z.as_user("bob")
        done = bob("connect", "mail", "--timeout", "20")
        assert done.exit_code == 0 and "Connected to mail" in plain(done.output), done.output
        listed = bob("connections", "--json")
        [conn] = json.loads(listed.output)
        assert conn["status"] == "active" and conn["read_scopes"] and conn["write_scopes"]
        assert bob("disconnect", conn["id"], "--yes").exit_code == 0
        assert json.loads(bob("connections", "--json").output)[0]["status"] == "revoked"
