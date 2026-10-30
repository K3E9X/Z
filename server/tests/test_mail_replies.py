"""A reply to a Z mail about a question answers it, with the sender's rights; anything else is refused (ADR 0034)."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from z_server import notifications
from z_server.db.models import AuditEvent, Event, Job, Question, Task

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import base_config, csrf, seed_packs, sign_in_as, use_jar

MAIL_SECRET = "mail-relay-" + "secret-for-tests"
Sent = list[tuple[list[str], str, str]]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Sent:
    mails: Sent = []
    monkeypatch.setattr(
        notifications, "_send_mail", lambda smtp, password, to, subject, text: mails.append((to, subject, text))
    )
    return mails


def _client(make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, tmp_path: Any, digest: dict[str, str]) -> Any:
    config = base_config(oidc.issuer, **{"notifications.digest": digest})
    client = make_client(config=config, llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "artifacts")
    seed_packs(client)
    return client


def _jars(client: TestClient, oidc: FakeGitLabOIDC, smtp: bool = True) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(client, oidc, u) for u in ("alice", "bob", "carol", "dave", "victor")}
    if smtp:
        use_jar(client, jars["alice"])
        resp = client.put(
            "/api/v1/admin/notifications",
            json={"targets": [], "smtp": {"host": "smtp.example.internal", "from": "z@example.internal"}},
            headers=csrf(client),
        )
        assert resp.status_code == 200, resp.text
    return jars


def _as(client: TestClient, jars: dict[str, dict[str, str]], user: str) -> dict[str, str]:
    use_jar(client, jars[user])
    return csrf(client)


def _email(client: TestClient, jars: dict[str, dict[str, str]], user: str, enabled: bool, events: list[str]) -> None:
    h = _as(client, jars, user)
    resp = client.put("/api/v1/me/notifications", json={"email": {"enabled": enabled, "events": events}}, headers=h)
    assert resp.status_code == 200, resp.text


def _run(client: TestClient, fn: Callable[[Any], Any]) -> Any:
    state = client.app.state.z  # type: ignore[attr-defined]

    async def go() -> Any:
        return await fn(state)

    return client.portal.call(go)  # type: ignore[union-attr]


def _db(client: TestClient, fn: Callable[[Any], Any]) -> Any:
    async def go(state: Any) -> Any:
        async with state.sessions() as db, db.begin():
            return await fn(db)

    return _run(client, go)


def _question(client: TestClient, h: dict[str, str], options: list[str] | None = None) -> tuple[str, str, str]:
    """bob's summarize mission (C2, team Cyber Ops), its job running, and a question from the agent."""
    created = client.post(
        "/api/v1/missions", json={"playbook": "summarize-file", "inputs": {"file": "n.md"}, "start": False}, headers=h
    )
    assert created.status_code == 201, created.text
    mission_id = str(created.json()["id"])
    up = client.post(
        f"/api/v1/missions/{mission_id}/files", files={"file": ("n.md", b"notes", "text/markdown")}, headers=h
    )
    assert up.status_code == 201, up.text
    assert client.post(f"/api/v1/missions/{mission_id}/start", json={}, headers=h).status_code == 200
    executor = RecordingExecutor()
    [job_id] = dispatch(client, executor)
    runner = ScriptedRunner.for_job(client, executor, job_id)
    runner.get_spec()
    body: dict[str, Any] = {"text": "Which tenant should I use for the export?"}
    if options:
        body["options"] = options
    asked = runner.call("POST", "/questions", json=body)
    assert asked.status_code == 201, asked.text
    return mission_id, job_id, str(asked.json()["id"])


def _deliver(client: TestClient) -> dict[str, int]:
    return dict(_run(client, notifications.tick))


def _addressed(sent: Sent, user: str) -> list[tuple[str, str]]:
    return [(subject, text) for to, subject, text in sent if to == [f"{user}@example.test"]]


# ---------------------------------------------------------------- replies to Z mails


@pytest.fixture
def mail_app(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, dict[str, dict[str, str]]]:
    monkeypatch.setenv("MAIL_RELAY", MAIL_SECRET)
    client = _client(make_client, oidc, tmp_path, {})
    jars = _jars(client, oidc)
    created = client.post(
        "/api/v1/channels",
        json={
            "kind": "email",
            "default_agent": "ticket-manager",
            "config": {"secret": "env:MAIL_RELAY"},
            "enabled": True,
        },
        headers=_as(client, jars, "alice"),
    )
    assert created.status_code == 201, created.text
    return client, jars


def _mail(client: TestClient, sender: str, subject: str, text: str) -> httpx.Response:
    client.cookies.clear()  # the relay carries no Z session
    raw = json.dumps({"from": f"<{sender}@example.test>", "subject": subject, "text": text}).encode()
    stamp = str(int(time.time()))
    sig = "sha256=" + hmac.new(MAIL_SECRET.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/v1/channels/email/inbound",
        content=raw,
        headers={"Content-Type": "application/json", "X-Z-Timestamp": stamp, "X-Z-Signature": sig},
    )


def _state_of(client: TestClient, question_id: str) -> tuple[str | None, str | None, int, str, int]:
    async def go(db: Any) -> tuple[str | None, str | None, int, str, int]:
        q = await db.get(Question, question_id)
        answered = (await db.execute(select(Event).where(Event.type == "question_answered"))).scalars()
        job = await db.get(Job, q.job_id)
        tasks = len(list((await db.execute(select(Task))).scalars()))
        return q.answer, q.answered_by, len(list(answered)), job.status, tasks

    return _db(client, go)  # type: ignore[no-any-return]


def test_a_reply_to_a_question_mail_answers_it_with_the_senders_rights(
    mail_app: tuple[TestClient, dict[str, dict[str, str]]], sent: Sent
) -> None:
    client, jars = mail_app
    _email(client, jars, "bob", True, ["question_asked"])
    _email(client, jars, "dave", True, [])
    mission_id, _, question_id = _question(client, _as(client, jars, "bob"), options=["Prod", "Staging"])
    _deliver(client)
    [(subject, text)] = _addressed(sent, "bob")
    tag = re.search(r"\[Z-Q:[^\]]+\]", subject)
    assert tag is not None and subject.startswith("[Z] An agent asks a question")
    assert "Reply to this e-mail with your answer on the first line" in text
    assert "Prod" not in text  # C2: the options stay in Z, like the question
    reply_subject = f"Re: {subject}"

    forged = re.sub(r"\.[0-9a-f]{24}\]", "." + "0" * 24 + "]", tag.group(0))
    other = tag.group(0).replace(question_id, "q_01JABCDEFGHJKMNPQRSTVWXYZ0")
    for bad in (forged, other, "[Z-Q:nonsense]"):
        resp = _mail(client, "bob", f"Re: [Z] An agent asks a question {bad}", "Staging")
        assert resp.status_code == 202 and resp.json()["task_id"] is None
    assert _state_of(client, question_id) == (None, None, 0, "needs_attention", 0)  # nothing changed, no task

    resp = _mail(client, "dave", reply_subject, "Staging")  # an auditor sees the mission but may not answer
    assert resp.status_code == 202 and resp.json()["task_id"] is None
    resp = _mail(client, "bob", reply_subject, "Maybe later")  # not one of the options
    assert resp.status_code == 202
    assert _state_of(client, question_id) == (None, None, 0, "needs_attention", 0)

    resp = _mail(client, "bob", reply_subject, "\n  staging\n\nOn Fri, Z wrote:\n> Which tenant should I use?\n")
    assert resp.status_code == 202 and resp.json()["task_id"] is None
    assert _state_of(client, question_id) == ("Staging", "bob", 1, "running", 0)
    again = _mail(client, "bob", reply_subject, "Staging")  # the relay delivers it twice
    assert again.status_code == 202 and _state_of(client, question_id)[2] == 1

    async def audit(db: Any) -> list[tuple[str, str, str | None, Any]]:
        rows = (await db.execute(select(AuditEvent).order_by(AuditEvent.seq))).scalars()
        return [
            (r.action, r.result, r.runs_as, r.after)
            for r in rows
            if r.action in {"question.answered", "channel.denied"}
        ]

    entries = _db(client, audit)
    answered = [e for e in entries if e[0] == "question.answered"]
    assert answered == [
        ("question.answered", "success", "bob", {"mission": mission_id, "length": 7, "channel": "email"})
    ]
    reasons = [e[3]["reason"] for e in entries if e[0] == "channel.denied"]
    assert reasons == ["unknown reply token"] * 3 + ["forbidden", "validation"]

    sent.clear()
    _deliver(client)
    [(_, refused)] = _addressed(sent, "dave")
    assert "may not answer this question" in refused
    bob_mails = [t for _, t in _addressed(sent, "bob")]
    assert any("this mail does not answer a question Z asked" in t for t in bob_mails)
    assert any("not one of the options: see them in Z" in t for t in bob_mails)
    assert any("Z recorded your answer" in t for t in bob_mails)
    assert all("Prod" not in t and "Staging" not in t for t in bob_mails)


def test_unknown_senders_get_nothing_and_plain_mail_stays_a_task(
    mail_app: tuple[TestClient, dict[str, dict[str, str]]], sent: Sent
) -> None:
    client, jars = mail_app
    _email(client, jars, "bob", True, ["question_asked"])
    _, _, question_id = _question(client, _as(client, jars, "bob"))
    _deliver(client)
    [(subject, _)] = _addressed(sent, "bob")
    resp = _mail(client, "mallory", f"Re: {subject}", "Prod")
    assert resp.status_code == 202 and resp.json()["task_id"] is None
    plain = _mail(client, "bob", "Please export the tenant list", "Thanks")
    assert plain.status_code == 202 and plain.json()["task_id"]
    answer, _, events_, _, tasks = _state_of(client, question_id)
    assert answer is None and events_ == 0 and tasks == 1
    sent.clear()
    _deliver(client)
    assert sent == []  # no backscatter to unknown senders
