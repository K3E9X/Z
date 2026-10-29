"""Google Workspace through the workplace pack connectors `google` (Gmail, Calendar, Drive) and `gchat`, on
each person's own account: Google's OAuth conventions (offline access without `offline_access`), reads with the
read grant, mails and events built and checked by Z, applied with the write grant, and no duplicate after a crash."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

CLIENTS = {"read_app": "read-client", "write_app": "write-client"}


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], tools: FakeTools, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> TestClient:
    from z_server.gateway import oauth

    tools.reset()
    monkeypatch.setenv("GOOGLE_API_URL", tools.url_for("google"))
    monkeypatch.setenv("GOOGLE_CHAT_API_URL", tools.url_for("google-chat"))
    monkeypatch.setenv("GOOGLE_READ_CLIENT_SECRET", "read-secret")
    monkeypatch.setenv("GOOGLE_WRITE_CLIENT_SECRET", "write-secret")
    monkeypatch.setattr(oauth, "GOOGLE_AUTHORIZE_URL", f"{tools.url_for('google')}/oauth2/auth")
    monkeypatch.setattr(oauth, "GOOGLE_TOKEN_URL", f"{tools.url_for('google')}/oauth2/token")
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "a")
    seed_packs(client)
    state = client.app.state.z  # type: ignore[attr-defined]

    async def configure() -> None:  # what the admin does: the pack ships placeholders for the OAuth client ids
        from z_server.db.models import Connector

        async with state.sessions() as db, db.begin():
            for row in (await db.execute(select(Connector).where(Connector.name.in_(["google", "gchat"])))).scalars():
                oauth_cfg = dict(row.manifest["oauth"])
                for app_name, client_id in CLIENTS.items():
                    oauth_cfg[app_name] = {**oauth_cfg[app_name], "client_id": client_id}
                row.manifest = {**row.manifest, "oauth": oauth_cfg}

    client.portal.call(configure)  # type: ignore[union-attr]
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}
    use_jar(app, jars["alice"])
    for name in ("google", "gchat"):
        ops = app.get(f"/api/v1/connectors/{name}/operations").json()
        app.post(
            f"/api/v1/connectors/{name}/approve-exposure",
            json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
            headers=csrf(app),
        ).raise_for_status()
    agent = {
        "name": "gsuite",
        "display_name": "Workspace helper",
        "handle": "gsuite",
        "version": "1.0.0",
        "role": "Works in my Google Workspace.",
        "model": "z-fast",
        "skills": [],
        "tools": ["gateway:google.*", "gateway:gchat.*"],
        "autonomy": "propose",
        "limits": {"max_steps": 10, "max_tokens": 10000, "timeout_s": 300},
    }
    assert app.post("/api/v1/agents", json=agent, headers=csrf(app)).status_code == 201
    return jars


def connect(app: TestClient, connector: str, purpose: str) -> None:
    start = app.post(f"/api/v1/me/connections/{connector}/oauth/start", json={"purpose": purpose}, headers=csrf(app))
    assert start.status_code == 200, start.text
    url = start.json()["redirect_url"]
    query = parse_qs(urlparse(url).query)
    assert query["access_type"] == ["offline"] and "consent" in query["prompt"][0].split()
    assert "offline_access" not in query["scope"][0] and "include_granted_scopes" not in query
    consent = httpx.get(url, follow_redirects=False)
    assert consent.status_code == 302, consent.text
    back = urlparse(consent.headers["location"])
    done = app.get(f"{back.path}?{back.query}", follow_redirects=False)
    assert done.headers["location"] == f"/settings/connections?connected={connector}", done.headers


def start(app: TestClient, jars: dict[str, dict[str, str]], writes: list[str], connectors: list[str]) -> ScriptedRunner:
    use_jar(app, jars["bob"])
    for name in connectors:
        for purpose in ("read", "write"):
            connect(app, name, purpose)
    plan = {
        "steps": [{"id": "s", "agent": "gsuite", "prompt": "Help", "credentials": "user", "writes": writes}],
        "writes": writes,
    }
    created = app.post(
        "/api/v1/missions",
        json={"title": "Help", "prompt": "Help", "plan": plan, "connectors": connectors},
        headers=csrf(app),
    )
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    return runner


def call(app: TestClient, runner: ScriptedRunner, name: str, args: dict[str, Any]) -> dict[str, Any]:
    resp = app.post(
        "/gateway/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
        headers={"Authorization": f"Bearer {runner.session}", "Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["result"])


def propose(runner: ScriptedRunner, connector: str, operation: str, payload: dict[str, Any]) -> Any:
    return runner.call(
        "POST",
        "/actions",
        json={"connector": connector, "operation": operation, "summary": "Do it", "payload": payload},
    )


def approve_and_apply(app: TestClient, jars: dict[str, dict[str, str]], action_id: str) -> dict[str, Any]:
    from z_server.gateway import executor

    use_jar(app, jars["carol"])
    app.post(f"/api/v1/actions/{action_id}/approve", json={}, headers=csrf(app)).raise_for_status()
    state = app.app.state.z  # type: ignore[attr-defined]
    return app.portal.call(executor.tick, state, state.gateway)  # type: ignore[union-attr,no-any-return]


def crash_and_recover(app: TestClient, action_id: str) -> dict[str, Any]:
    """The executor died between the write and its bookkeeping: the action is left `applying`."""
    from z_server.db.models import ProposedAction
    from z_server.gateway import executor

    state = app.app.state.z  # type: ignore[attr-defined]

    async def crash() -> None:
        async with state.sessions() as db, db.begin():
            await db.execute(
                update(ProposedAction)
                .where(ProposedAction.id == action_id)
                .values(status="applying", updated_at=ProposedAction.updated_at - timedelta(hours=1))
            )

    app.portal.call(crash)  # type: ignore[union-attr]
    report = app.portal.call(executor.tick, state, state.gateway)  # type: ignore[union-attr]
    assert report["recovered"] == [action_id], report
    return dict(app.get(f"/api/v1/actions/{action_id}").json())


def test_reads_use_the_starters_read_grant_and_refresh(
    app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools
) -> None:
    runner = start(app, jars, [], ["google"])
    mail = call(app, runner, "google__gmail__messages__list", {"q": "access review", "maxResults": 5})
    assert mail["isError"] is False and "18c0a1" in mail["content"][0]["text"], mail
    one = call(app, runner, "google__gmail__messages__get", {"id": "18c0a1", "format": "full"})
    assert "Quarterly access review" in one["content"][0]["text"]
    doc = call(app, runner, "google__drive__files__get_text", {"fileId": "doc1", "mimeType": "text/plain"})
    assert doc["isError"] is False and "SSO everywhere" in doc["content"][0]["text"]
    agenda = call(app, runner, "google__calendar__events__list", {"calendarId": "primary", "singleEvents": True})
    assert "Patch window" in agenda["content"][0]["text"]
    assert "ya29." not in str([mail, one, doc, agenda])  # tokens never reach the agent

    httpx.post(f"{tools.url_for('google')}/_fake/expire").raise_for_status()
    from z_server.db.models import Credential

    state = app.app.state.z  # type: ignore[attr-defined]

    async def due() -> None:
        async with state.sessions() as db, db.begin():
            await db.execute(update(Credential).where(Credential.scope == "user").values(expires_at=None))

    app.portal.call(due)  # type: ignore[union-attr]
    again = call(app, runner, "google__gmail__messages__list", {"labelIds": "INBOX"})
    assert again["isError"] is False, again  # refreshed: Google keeps the refresh token, Z keeps it too
    sent = call(
        app, runner, "google__gmail__messages__send", {"body": {"to": ["x@example.test"], "subject": "s", "body": "b"}}
    )
    assert sent["isError"] is True and tools.writes("google.gmail.messages.send") == []  # writes are proposals


def test_a_mail_is_built_by_z_previewed_sent_once(
    app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools
) -> None:
    runner = start(app, jars, ["google.gmail.messages.send"], ["google"])
    reply = {
        "to": ["bob@example.test"],
        "cc": ["carol@example.test"],
        "subject": "Re: Quarterly access review",
        "body": "Reviewed: two stale accounts to remove.",
        "thread_id": "18c0a1",
        "in_reply_to": "<qar-1@example.test>",
    }
    for sneaky in (
        {**reply, "bcc": ["eve@example.test"]},  # no hidden recipient
        {**reply, "subject": "Hi\r\nBcc: eve@example.test"},  # no header injection
        {**reply, "to": ["Eve <eve@example.test>, mallory@example.test"]},  # one plain address per entry
        # RFC 2047 encoded words are decoded when the headers are parsed: hidden recipients, unseen subject text
        {**reply, "cc": ["=?utf-8?b?c3B5QGV2aWwuY29tLCBjb2xsZWFndWU=?=@corp.example"]},
        {**reply, "subject": "=?utf-8?b?UTMgcmV2ZW51ZTogLTQwJQ==?="},
    ):
        refused = propose(runner, "google", "google.gmail.messages.send", {"body": sneaky})
        assert refused.status_code == 422, (sneaky, refused.text)
    proposed = propose(runner, "google", "google.gmail.messages.send", {"body": reply})
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    assert action["preview"] == {
        "kind": "mail",
        "to": ["bob@example.test"],
        "cc": ["carol@example.test"],
        "subject": "Re: Quarterly access review",
        "body": "Reviewed: two stale accounts to remove.",
        "in_reply_to": "<qar-1@example.test>",
    }
    assert approve_and_apply(app, jars, action["id"])["applied"] == [action["id"]]
    [write] = tools.writes("google.gmail.messages.send")
    headers = write["data"]["headers"]
    assert write["data"]["as"] == "alice@example.test" and write["data"]["threadId"] == "18c0a1"
    assert headers["To"] == "bob@example.test" and headers["Cc"] == "carol@example.test"
    assert headers["In-Reply-To"] == "<qar-1@example.test>" and headers["X-Z-Action"] == action["id"]
    assert "Bcc" not in headers and write["data"]["text"].strip() == "Reviewed: two stale accounts to remove."

    recovered = crash_and_recover(app, action["id"])
    assert recovered["status"] == "applied" and recovered["result"]["recovered"] is True
    assert recovered["result"]["id"] == write["data"]["id"]
    assert len(tools.writes("google.gmail.messages.send")) == 1


def test_an_event_is_created_once_and_a_planted_copy_is_ignored(
    app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools
) -> None:
    from tests.fakes.fake_tools.google import google_seed

    runner = start(app, jars, ["google.calendar.events.insert"], ["google"])
    event = {
        "summary": "Access review",
        "start": {"dateTime": "2026-11-03T10:00:00", "timeZone": "Europe/Paris"},
        "end": {"dateTime": "2026-11-03T10:30:00", "timeZone": "Europe/Paris"},
        "attendees": [{"email": "bob@example.test"}],
    }
    refused = propose(
        runner, "google", "google.calendar.events.insert", {"calendarId": "primary", "body": {**event, "id": "x1234"}}
    )
    assert refused.status_code == 422  # the agent cannot choose ids or extended properties
    for odd in ({"calendarId": "..", "body": event}, {"calendarId": "primary", "body": event, "colorId": "1"}):
        assert propose(runner, "google", "google.calendar.events.insert", odd).status_code == 422, odd
    proposed = propose(
        runner,
        "google",
        "google.calendar.events.insert",
        {"calendarId": "primary", "sendUpdates": "all", "body": event},
    )
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    preview = action["preview"]
    assert preview["kind"] == "event" and preview["title"] == "Access review"
    assert preview["start"] == "2026-11-03T10:00:00 Europe/Paris" and preview["attendees"] == ["bob@example.test"]
    assert "invitations sent to every attendee" in preview["body"]

    # someone who learnt the action id sends an invitation carrying the marker: it is not this account's event
    seed = google_seed()
    marker = {"extendedProperties": {"private": {"z_action": action["id"]}}, "summary": "Access review"}
    planted = {**seed["events"][0], "id": "planted1", **marker}
    # ...or a delegate creates it in this calendar: the account organises it (organizer.self) but did not create it
    delegated = {**planted, "id": "planted2", "organizer": {"email": "alice@example.test"}}
    seed["events"] += [planted, delegated]
    tools.seed({"google": seed})

    assert approve_and_apply(app, jars, action["id"])["applied"] == [action["id"]]
    [write] = tools.writes("google.calendar.events.insert")
    assert write["data"]["sendUpdates"] == "all" and write["data"]["summary"] == "Access review"
    assert write["data"]["extendedProperties"] == {"private": {"z_action": action["id"]}}

    recovered = crash_and_recover(app, action["id"])
    assert recovered["status"] == "applied" and recovered["result"]["recovered"] is True
    assert recovered["result"]["id"] == write["data"]["id"] not in {"planted1", "planted2"}
    assert len(tools.writes("google.calendar.events.insert")) == 1


def test_a_chat_post_is_idempotent_by_its_request_id(
    app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools
) -> None:
    runner = start(app, jars, ["gchat.messages.create"], ["gchat"])
    spaces = call(app, runner, "gchat__spaces__list", {})
    assert "SecOps" in spaces["content"][0]["text"]
    payload = {
        "space": "AAAAspace01",
        "messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD",
        "body": {"text": "edge-01 belongs to netops", "thread": {"name": "spaces/AAAAspace01/threads/t1"}},
    }
    proposed = propose(runner, "gchat", "gchat.messages.create", payload)
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    assert action["preview"] == {
        "kind": "chat_message",
        "platform": "google_chat",
        "target": "spaces/AAAAspace01 (reply in spaces/AAAAspace01/threads/t1)",
        "text": "edge-01 belongs to netops",
    }
    assert approve_and_apply(app, jars, action["id"])["applied"] == [action["id"]]
    [write] = tools.writes("gchat.messages.create")
    assert write["data"]["thread"]["name"] == "spaces/AAAAspace01/threads/t1"

    recovered = crash_and_recover(app, action["id"])  # posted again with the same request id: Chat returns the first
    assert recovered["status"] == "applied" and recovered["result"]["name"] == write["data"]["name"]
    assert len(tools.writes("gchat.messages.create")) == 1


def test_google_connectors_have_no_client_credentials(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    use_jar(app, jars["alice"])
    manifest = {
        "name": "gmailshared",
        "kind": "rest",
        "base_url": "https://www.googleapis.com",
        "oauth": {
            "provider": "google",
            "read_app": {"client_id": "a", "secret": "env:A"},
            "write_app": {"client_id": "b", "secret": "env:B"},
        },
        "auth": {"read": {"service": {"flow": "client_credentials", "app": "read_app"}}},
        "declared": [
            {"id": "messages.list", "description": "Mail", "method": "GET", "path": "/gmail/v1/users/me/messages"}
        ],
        "data_class": "C2",
    }
    refused = app.post("/api/v1/connectors", json=manifest, headers=csrf(app))
    assert refused.status_code == 422 and "no client credentials grant" in refused.text, refused.text


def test_the_mail_is_rfc_2822_with_the_marker() -> None:
    from z_server.gateway.writers import Google, WriteError

    raw = Google.mime({"to": ["a@example.test"], "subject": "Été", "body": "Bonjour"}, "act_1").decode()
    assert "X-Z-Action: act_1" in raw and "\r\n" in raw and "Subject: =?utf-8?" in raw
    assert base64.urlsafe_b64encode(raw.encode())  # what goes into `raw`
    for bad in (
        {"to": ["a@example.test"], "subject": "x\nBcc: e@example.test"},
        {"to": ["a@example.test"], "subject": "=?utf-8?q?hidden?="},
        {"to": ["=?utf-8?b?c3B5QGV2aWwuY29tLCBj?=@corp.example"], "subject": "s"},
        {"to": ["a@example.test, b@example.test"], "subject": "s"},  # one address per entry, checked back
    ):
        with pytest.raises(WriteError):
            Google.mime({**bad, "body": ""}, "act_1")


def test_a_chat_reply_into_another_spaces_thread_is_refused() -> None:
    import asyncio
    from types import SimpleNamespace

    from z_server.gateway.writers import GoogleChat, WriteError

    action = SimpleNamespace(
        id="act_1",
        payload={"space": "AAAA", "body": {"text": "hi", "thread": {"name": "spaces/BBBB/threads/t1"}}},
    )
    op = SimpleNamespace(local_id="messages.create")
    with pytest.raises(WriteError, match="not in the space"):
        asyncio.run(GoogleChat().apply(None, None, op, action, {}))  # type: ignore[arg-type]
