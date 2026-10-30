"""E-mail digests: daily decisions and weekly report, once per period, capped content (ADR 0034)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from z_server import digests, notifications
from z_server.db.models import Mission, NotificationOutbox, ProposedAction
from z_server.intake.cron import Cron

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import base_config, csrf, seed_packs, sign_in_as, use_jar

DAILY = "0 9 * * *"
WEEKLY = "0 16 * * 5"
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


def _pending_action(client: TestClient, mission_id: str, job_id: str, *, four_eyes: bool = False) -> str:
    action_id = f"act_digest{int(four_eyes)}"

    async def add(db: Any) -> None:
        db.add(
            ProposedAction(
                id=action_id,
                mission_id=mission_id,
                job_id=job_id,
                agent="summarizer",
                connector="jira",
                operation="jira.issue.comment",
                op_class="write",
                summary="Comment on SEC-42 with the export plan",
                payload={},
                preview={},
                runs_as={},
                status="pending",
                required_approvals=1,
                four_eyes=four_eyes,
                idempotency_key=f"idem-{action_id}",
            )
        )

    _db(client, add)
    return action_id


def _set_class(client: TestClient, mission_id: str, data_class: str) -> None:
    async def go(db: Any) -> None:
        mission = await db.get(Mission, mission_id)
        mission.data_class = data_class

    _db(client, go)


def _digest_tick(client: TestClient, at: datetime) -> dict[str, int]:
    return dict(_run(client, lambda state: digests.tick(state, at)))


def _deliver(client: TestClient) -> dict[str, int]:
    return dict(_run(client, notifications.tick))


def _addressed(sent: Sent, user: str) -> list[tuple[str, str]]:
    return [(subject, text) for to, subject, text in sent if to == [f"{user}@example.test"]]


# ---------------------------------------------------------------- digests


def test_daily_digest_lists_decisions_once_per_period_with_capped_content(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, tmp_path: Any, sent: Sent
) -> None:
    client = _client(make_client, oidc, tmp_path, {"daily_decisions": DAILY})
    jars = _jars(client, oidc)
    for user in ("bob", "carol", "victor"):  # victor (viewer) has nothing to decide; dave never turned email on
        _email(client, jars, user, True, [])
    mission_id, job_id, _ = _question(client, _as(client, jars, "bob"))
    action_id = _pending_action(client, mission_id, job_id)
    _pending_action(client, mission_id, job_id, four_eyes=True)  # bob started the mission: never his to approve

    start = datetime.now(UTC)
    due = Cron(DAILY).next_after(start)
    assert _digest_tick(client, start) == {}  # first sight: from now on
    assert _digest_tick(client, due - timedelta(minutes=1)) == {}
    assert _digest_tick(client, due + timedelta(minutes=1)) == {"daily_decisions": 2}  # bob and carol
    assert _digest_tick(client, due + timedelta(minutes=30)) == {}  # same period
    _deliver(client)
    assert {to[0] for to, _, _ in sent} == {"bob@example.test", "carol@example.test"}
    [(subject, carol)] = _addressed(sent, "carol")
    assert subject == "[Z] Decisions waiting for you"
    assert "2 write(s) to approve and 1 question(s) to answer" in carol
    assert f"https://testserver/review?action={action_id}" in carol and f"/missions/{mission_id}" in carol
    assert "SEC-42" not in carol and "Which tenant" not in carol and "details in Z (data class C2)" in carol
    [(_, bob)] = _addressed(sent, "bob")
    assert "0 write(s) to approve and 1 question(s) to answer" in bob

    # next day: the mission is C1 (content may go by mail) and carol turned e-mail off
    _set_class(client, mission_id, "C1")
    _email(client, jars, "carol", False, [])
    sent.clear()
    next_day = due + timedelta(days=1, minutes=1)
    assert _digest_tick(client, next_day) == {"daily_decisions": 1}
    _deliver(client)
    [(_, bob)] = _addressed(sent, "bob")

    async def title(db: Any) -> str:
        return str((await db.get(Mission, mission_id)).title)

    assert f"Question to answer: Which tenant should I use for the export? ({_db(client, title)})" in bob
    assert _addressed(sent, "carol") == []


def test_digests_are_enqueued_once_with_concurrent_workers(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, tmp_path: Any, sent: Sent
) -> None:
    client = _client(make_client, oidc, tmp_path, {"daily_decisions": DAILY})
    jars = _jars(client, oidc)
    _email(client, jars, "bob", True, [])
    _question(client, _as(client, jars, "bob"))
    start = datetime.now(UTC)
    due = Cron(DAILY).next_after(start) + timedelta(minutes=1)
    _digest_tick(client, start)

    async def both(state: Any) -> list[dict[str, int]]:
        return list(await asyncio.gather(digests.tick(state, due), digests.tick(state, due)))

    reports = _run(client, both)
    assert sum(r.get("daily_decisions", 0) for r in reports) == 1

    async def rows(db: Any) -> list[str]:
        return [r.kind for r in (await db.execute(select(NotificationOutbox))).scalars() if r.kind == "digest"]

    assert _db(client, rows) == ["digest"]


def test_weekly_report_counts_the_missions_of_the_week_and_skips_empty_ones(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, tmp_path: Any, sent: Sent
) -> None:
    client = _client(make_client, oidc, tmp_path, {"weekly_report": WEEKLY})
    jars = _jars(client, oidc)
    for user in ("bob", "carol"):
        _email(client, jars, user, True, [])
    mission_id, _, _ = _question(client, _as(client, jars, "bob"))

    async def tokens(db: Any) -> None:
        mission = await db.get(Mission, mission_id)
        mission.tokens_in, mission.tokens_out = 1200, 300

    _db(client, tokens)
    start = datetime.now(UTC)
    friday = Cron(WEEKLY).next_after(start)
    _digest_tick(client, start)
    assert _digest_tick(client, friday + timedelta(minutes=2)) == {"weekly_report": 1}  # carol had no mission
    _deliver(client)
    [(subject, text)] = _addressed(sent, "bob")
    assert subject == "[Z] Your week in Z"
    assert "Missions: 1 (waiting 1)" in text and "Tokens: 1 500" in text and "Approvals you gave: 0" in text
    assert "Which tenant" not in text  # counts only
    assert _addressed(sent, "carol") == []


def test_no_digest_without_smtp(make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, tmp_path: Any) -> None:
    client = _client(make_client, oidc, tmp_path, {"daily_decisions": DAILY})
    jars = _jars(client, oidc, smtp=False)
    _email(client, jars, "bob", True, [])
    _question(client, _as(client, jars, "bob"))
    start = datetime.now(UTC)
    _digest_tick(client, start)
    assert _digest_tick(client, Cron(DAILY).next_after(start) + timedelta(minutes=1)) == {}

    async def rows(db: Any) -> list[str]:
        return [r.kind for r in (await db.execute(select(NotificationOutbox))).scalars() if r.kind == "digest"]

    assert _db(client, rows) == []
