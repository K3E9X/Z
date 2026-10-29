"""Personal OAuth connections: PKCE flow, sealed tokens, refresh, revoke; user-scope reads in the gateway and
user-scope writes in the executor with the right app; app-only (client credentials) service tokens."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as t:
        yield t


def mail(tools: FakeTools) -> dict[str, Any]:
    base = tools.url_for("m365")
    return {
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
            "read": {
                "user": {"flow": "authorization_code_pkce", "app": "read_app", "scopes": ["Mail.Read"]},
                "service": {"flow": "client_credentials", "app": "read_app", "scopes": ["api.read"]},
            },
            "write": {"user": {"flow": "authorization_code_pkce", "app": "write_app", "scopes": ["Mail.Send"]}},
        },
        "declared": [
            {
                "id": "messages.list",
                "description": "My mail",
                "method": "GET",
                "path": "/me/messages",
                "input_schema": {"type": "object"},
            },
            {
                "id": "users.messages.list",
                "description": "A shared mailbox",
                "method": "GET",
                "path": "/users/{user}/messages",
                "input_schema": {"type": "object", "required": ["user"], "properties": {"user": {"type": "string"}}},
            },
            {
                "id": "mail.send",
                "description": "Send a mail",
                "method": "POST",
                "path": "/me/sendMail",
                "input_schema": {
                    "type": "object",
                    "required": ["message"],
                    "properties": {"message": {"type": "object"}},
                },
            },
        ],
        "operations": {
            "default_exposed": False,
            "rules": [
                {"match": "messages.list", "class": "read", "exposed": True},
                {"match": "users.messages.list", "class": "read", "exposed": True},
                {"match": "mail.send", "class": "write", "exposed": True},
            ],
        },
        "data_class": "C2",
    }


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], tools: FakeTools, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> TestClient:
    tools.reset()
    monkeypatch.setenv("MAIL_READ_SECRET", "read-secret")
    monkeypatch.setenv("MAIL_WRITE_SECRET", "write-secret")
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "a")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC, tools: FakeTools) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}
    use_jar(app, jars["alice"])
    created = app.post("/api/v1/connectors", json=mail(tools), headers=csrf(app))
    assert created.status_code == 201, created.text
    ops = app.get("/api/v1/connectors/mail/operations").json()
    app.post(
        "/api/v1/connectors/mail/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": True} for o in ops]},
        headers=csrf(app),
    ).raise_for_status()
    agent = {
        "name": "mailer",
        "display_name": "Mailer",
        "handle": "mailer",
        "version": "1.0.0",
        "role": "Triages a mailbox.",
        "model": "z-fast",
        "skills": [],
        "tools": ["gateway:mail.*"],
        "autonomy": "propose",
        "limits": {"max_steps": 10, "max_tokens": 10000, "timeout_s": 300},
    }
    assert app.post("/api/v1/agents", json=agent, headers=csrf(app)).status_code == 201
    return jars


def connect(app: TestClient, purpose: str) -> httpx.Response:
    start = app.post("/api/v1/me/connections/mail/oauth/start", json={"purpose": purpose}, headers=csrf(app))
    assert start.status_code == 200, start.text
    url = start.json()["redirect_url"]
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"] and "offline_access" in query["scope"][0]
    consent = httpx.get(url, follow_redirects=False)  # the provider (fake) consents and redirects back
    assert consent.status_code == 302, consent.text
    back = urlparse(consent.headers["location"])
    return app.get(f"{back.path}?{back.query}", follow_redirects=False)


def test_unsafe_oauth_manifests_are_refused(app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools) -> None:
    same = mail(tools) | {"name": "mailtwo"}
    same["oauth"] = {**same["oauth"], "write_app": {"client_id": "read-client", "secret": "env:MAIL_WRITE_SECRET"}}
    assert "different registrations" in app.post("/api/v1/connectors", json=same, headers=csrf(app)).text
    plain = mail(tools) | {"name": "mailthree"}
    plain["oauth"] = {**plain["oauth"], "read_app": {"client_id": "read-client", "secret": "s3cr3t-value"}}
    refused = app.post("/api/v1/connectors", json=plain, headers=csrf(app))
    assert refused.status_code == 422 and "s3cr3t-value" not in refused.text


def test_connect_use_refresh_and_revoke(app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools) -> None:
    use_jar(app, jars["bob"])
    for purpose in ("read", "write"):
        back = connect(app, purpose)
        assert back.status_code == 302 and back.headers["location"] == "/settings/connections?connected=mail"
    [conn] = app.get("/api/v1/me/connections").json()
    shown = app.get("/api/v1/connectors/mail").json()
    assert all(c["scope"] != "user" for c in shown["credentials"])  # never anyone's personal grant
    assert conn["status"] == "active" and conn["read_scopes"] and conn["write_scopes"]
    replay = connect(app, "read")  # a second consent replaces the read grant, never duplicates it
    assert replay.status_code == 302
    assert len(app.get("/api/v1/me/connections").json()) == 1

    # a user-scope step reads bob's mailbox with bob's read token
    writes = ["mail.mail.send"]
    plan = {
        "steps": [
            {
                "id": "triage",
                "agent": "mailer",
                "prompt": "Triage my mail",
                "credentials": "user",
                "writes": writes,
            }
        ],
        "writes": writes,
    }
    created = app.post(
        "/api/v1/missions",
        json={"title": "Mail", "prompt": "Mail", "plan": plan, "connectors": ["mail"]},
        headers=csrf(app),
    )
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()

    from z_server.gateway import core

    state = app.app.state.z  # type: ignore[attr-defined]
    gw: core.Gateway = state.gateway
    tools_list = [t["name"] for t in runner.spec["tools"]["gateway"]]
    assert "mail__messages__list" in tools_list, tools_list
    outcome = app.portal.call(gw.call, runner.session, "mail__messages__list", {})  # type: ignore[union-attr]
    assert outcome.ok and "alice@example.test" in outcome.text and "Quarterly access review" in outcome.text, (
        outcome.text
    )
    assert "at-" not in outcome.text  # tokens never reach the agent

    # an expired access token is refreshed transparently (and the old one never reused)
    httpx.post(f"{tools.url_for('m365')}/_fake/expire").raise_for_status()
    from sqlalchemy import update
    from z_server.db.models import Credential

    async def expire_rows() -> None:
        async with state.sessions() as db, db.begin():
            await db.execute(update(Credential).where(Credential.scope == "user").values(expires_at=None))

    app.portal.call(expire_rows)  # type: ignore[union-attr]
    again = app.portal.call(gw.call, runner.session, "mail__messages__list", {})  # type: ignore[union-attr]
    assert again.ok, again.text

    # the write goes out with bob's write grant, through review and the executor
    proposed = runner.call(
        "POST",
        "/actions",
        json={
            "connector": "mail",
            "operation": "mail.mail.send",
            "summary": "reply",
            "payload": {"message": {"subject": "Re: review", "to": "carol@example.test"}},
        },
    )
    assert proposed.status_code == 201, proposed.text
    assert proposed.json()["runs_as"]["scope"] == "user"
    use_jar(app, jars["carol"])
    assert app.post(f"/api/v1/actions/{proposed.json()['id']}/approve", json={}, headers=csrf(app)).status_code == 200
    from z_server.gateway import executor as ex

    report = app.portal.call(ex.tick, state, gw)  # type: ignore[union-attr]
    assert report["applied"] == [proposed.json()["id"]], app.get(f"/api/v1/actions/{proposed.json()['id']}").json()
    [sent] = tools.writes("m365.mail.send")
    assert sent["data"]["as"] == "alice@example.test" and sent["data"]["message"]["subject"] == "Re: review"

    # revoke: tokens deleted, user-scope calls stop
    use_jar(app, jars["bob"])
    assert app.delete(f"/api/v1/me/connections/{conn['id']}", headers=csrf(app)).status_code == 204
    assert app.get("/api/v1/me/connections").json()[0]["status"] == "revoked"
    denied = app.portal.call(gw.call, runner.session, "mail__messages__list", {})  # type: ignore[union-attr]
    assert not denied.ok and "no active read connection" in denied.text


def test_callback_rejects_replays_and_errors(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    use_jar(app, jars["bob"])
    assert (
        app.get("/api/v1/oauth/callback?state=zo_nope&code=x", follow_redirects=False)
        .headers["location"]
        .endswith("error=this%20sign-in%20link%20expired%20or%20was%20already%20used")
    )
    start = app.post("/api/v1/me/connections/mail/oauth/start", json={"purpose": "read"}, headers=csrf(app)).json()
    state = parse_qs(urlparse(start["redirect_url"]).query)["state"][0]
    use_jar(app, jars["carol"])  # someone else's browser finishes bob's flow
    wrong = app.get(f"/api/v1/oauth/callback?state={state}&code=abc", follow_redirects=False)
    assert "as the person who started the connection" in wrong.headers["location"].replace("%20", " ")
    assert app.get("/api/v1/me/connections").json() == []

    # a browser with no Z session (a victim lured to bob's link after consenting) cannot attach its grant to bob
    use_jar(app, jars["bob"])
    start = app.post("/api/v1/me/connections/mail/oauth/start", json={"purpose": "read"}, headers=csrf(app)).json()
    consent = httpx.get(start["redirect_url"], follow_redirects=False)
    back = urlparse(consent.headers["location"])
    app.cookies.clear()
    lured = app.get(f"{back.path}?{back.query}", follow_redirects=False)
    assert "as the person who started the connection" in lured.headers["location"].replace("%20", " ")
    use_jar(app, jars["bob"])
    assert app.get("/api/v1/me/connections").json() == []


def test_app_only_service_token(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    from z_server.db.models import Connector, Operation

    state = app.app.state.z  # type: ignore[attr-defined]

    async def call() -> Any:
        async with state.sessions() as db:
            connector = (await db.execute(Connector.__table__.select().where(Connector.name == "mail"))).first()
            row = await db.get(Connector, connector.id)
            op = await db.get(Operation, "mail.users.messages.list")
        return await state.gateway.invoke(row, op, {"user": "secops@example.test"}, "read")

    inv = app.portal.call(call)  # type: ignore[union-attr]
    assert inv.ok and inv.identity == "app:read_app" and inv.body["value"][0]["owner"] == "secops@example.test"
