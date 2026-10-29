"""Microsoft 365 through the workplace pack connector `m365` (shipped Graph subset), pointed at the fake identity
platform and Graph: reads with the starter's read grant, closed write bodies with readable previews, and an event
created once even when the executor crashes after the call (Graph `transactionId`)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], tools: FakeTools, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> TestClient:
    tools.reset()
    monkeypatch.setenv("M365_READ_SECRET", "read-secret")
    monkeypatch.setenv("M365_WRITE_SECRET", "write-secret")
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "a")
    seed_packs(client)
    state = client.app.state.z  # type: ignore[attr-defined]
    base = tools.url_for("m365")

    async def configure() -> None:  # what the admin fills in: the pack ships placeholders (tenant, apps, vault)
        from z_server.db.models import Connector

        async with state.sessions() as db, db.begin():
            row = (await db.execute(select(Connector).where(Connector.name == "m365"))).scalar_one()
            row.base_url = f"{base}/v1.0"
            row.manifest = {
                **row.manifest,
                "base_url": f"{base}/v1.0",
                "oauth": {
                    "authorize_url": f"{base}/oauth2/authorize",
                    "token_url": f"{base}/oauth2/token",
                    "read_app": {"client_id": "read-client", "secret": "env:M365_READ_SECRET"},
                    "write_app": {"client_id": "write-client", "secret": "env:M365_WRITE_SECRET"},
                },
            }

    client.portal.call(configure)  # type: ignore[union-attr]
    return client


@pytest.fixture
def runner(app: TestClient, oidc: FakeGitLabOIDC) -> ScriptedRunner:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}
    use_jar(app, jars["alice"])
    ops = app.get("/api/v1/connectors/m365/operations").json()
    app.post(
        "/api/v1/connectors/m365/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(app),
    ).raise_for_status()
    agent = {
        "name": "outlook",
        "display_name": "Outlook helper",
        "handle": "outlook",
        "version": "1.0.0",
        "role": "Works in my mailbox and calendar.",
        "model": "z-fast",
        "skills": [],
        "tools": ["gateway:m365.*"],
        "autonomy": "propose",
        "limits": {"max_steps": 10, "max_tokens": 10000, "timeout_s": 300},
    }
    assert app.post("/api/v1/agents", json=agent, headers=csrf(app)).status_code == 201
    use_jar(app, jars["bob"])
    for purpose in ("read", "write"):
        start = app.post("/api/v1/me/connections/m365/oauth/start", json={"purpose": purpose}, headers=csrf(app))
        consent = httpx.get(start.json()["redirect_url"], follow_redirects=False)
        back = urlparse(consent.headers["location"])
        assert app.get(f"{back.path}?{back.query}", follow_redirects=False).status_code == 302
    writes = ["m365.mail.send", "m365.calendar.events.create"]
    plan = {
        "steps": [{"id": "s", "agent": "outlook", "prompt": "Help", "credentials": "user", "writes": writes}],
        "writes": writes,
    }
    created = app.post(
        "/api/v1/missions",
        json={"title": "Help", "prompt": "Help", "plan": plan, "connectors": ["m365"]},
        headers=csrf(app),
    )
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    scripted = ScriptedRunner.for_job(app, executor, job_id)
    scripted.get_spec()
    app.cookies.clear()
    use_jar(app, jars["carol"])  # the reviewer
    return scripted


def tick(app: TestClient) -> dict[str, Any]:
    from z_server.gateway import executor

    state = app.app.state.z  # type: ignore[attr-defined]
    return app.portal.call(executor.tick, state, state.gateway)  # type: ignore[union-attr,no-any-return]


def propose(runner: ScriptedRunner, operation: str, payload: dict[str, Any]) -> Any:
    return runner.call(
        "POST", "/actions", json={"connector": "m365", "operation": operation, "summary": "Do it", "payload": payload}
    )


def to(*addresses: str) -> list[dict[str, Any]]:
    return [{"emailAddress": {"address": a}} for a in addresses]


def test_mail_reads_and_a_closed_previewed_send(app: TestClient, runner: ScriptedRunner, tools: FakeTools) -> None:
    from z_server.gateway import core

    state = app.app.state.z  # type: ignore[attr-defined]
    gw: core.Gateway = state.gateway
    mail = app.portal.call(gw.call, runner.session, "m365__mail__list", {})  # type: ignore[union-attr]
    assert mail.ok and "Quarterly access review" in mail.text, mail.text

    message = {
        "subject": "Re: Quarterly access review",
        "body": {"contentType": "Text", "content": "Two stale accounts to remove."},
        "toRecipients": to("bob@example.test"),
    }
    hidden = propose(runner, "m365.mail.send", {"body": {"message": {**message, "bccRecipients": to("eve@x.test")}}})
    assert hidden.status_code == 422, hidden.text
    proposed = propose(runner, "m365.mail.send", {"body": {"message": message}})
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    assert action["preview"] == {
        "kind": "mail",
        "to": ["bob@example.test"],
        "subject": "Re: Quarterly access review",
        "body": "Two stale accounts to remove.",
    }
    assert app.post(f"/api/v1/actions/{action['id']}/approve", json={}, headers=csrf(app)).status_code == 200
    assert tick(app)["applied"] == [action["id"]]
    [sent] = tools.writes("m365.mail.send")
    assert sent["data"]["message"]["toRecipients"] == to("bob@example.test") and "bccRecipients" not in sent["data"]


def test_an_event_is_created_once_after_a_crash(app: TestClient, runner: ScriptedRunner, tools: FakeTools) -> None:
    from z_server.db.models import ProposedAction

    event = {
        "subject": "Access review",
        "start": {"dateTime": "2026-11-03T10:00:00", "timeZone": "Romance Standard Time"},
        "end": {"dateTime": "2026-11-03T10:30:00", "timeZone": "Romance Standard Time"},
        "attendees": [{"emailAddress": {"address": "bob@example.test"}, "type": "required"}],
    }
    refused = propose(runner, "m365.calendar.events.create", {"body": {**event, "transactionId": "mine"}})
    assert refused.status_code == 422  # the transaction id is Z's
    proposed = propose(runner, "m365.calendar.events.create", {"body": event})
    assert proposed.status_code == 201, proposed.text
    action = proposed.json()
    assert action["preview"]["kind"] == "event" and action["preview"]["attendees"] == ["bob@example.test"]
    assert app.post(f"/api/v1/actions/{action['id']}/approve", json={}, headers=csrf(app)).status_code == 200
    assert tick(app)["applied"] == [action["id"]]
    [write] = tools.writes("m365.calendar.events.create")
    assert write["data"]["transactionId"] == action["id"]

    state = app.app.state.z  # type: ignore[attr-defined]

    async def crash() -> None:
        async with state.sessions() as db, db.begin():
            await db.execute(
                update(ProposedAction)
                .where(ProposedAction.id == action["id"])
                .values(status="applying", updated_at=ProposedAction.updated_at - timedelta(hours=1))
            )

    app.portal.call(crash)  # type: ignore[union-attr]
    assert tick(app)["recovered"] == [action["id"]]
    recovered = app.get(f"/api/v1/actions/{action['id']}").json()
    assert recovered["status"] == "applied" and recovered["result"]["id"] == write["data"]["id"]
    assert len(tools.writes("m365.calendar.events.create")) == 1
