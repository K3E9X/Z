"""Notifications (ADR 0023): transactional outbox, targets, content limits, signatures, retries, budget alerts."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from z_server import events, notifications
from z_server.db.models import Mission, NotificationOutbox

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.server import csrf, seed_packs, sign_in_as


class Receiver:
    """A local webhook endpoint that records requests and answers with the queued status codes."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str], bytes]] = []
        self.statuses: list[int] = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                receiver.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                self.send_response(receiver.statuses.pop(0) if receiver.statuses else 200)
                self.end_headers()

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def bodies(self, path: str) -> list[Any]:
        return [json.loads(b) for p, _, b in self.requests if p == path]


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    r = Receiver()
    yield r
    r.server.shutdown()


@pytest.fixture
def app(make_client: Callable[..., TestClient], tmp_path: Any) -> TestClient:
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "artifacts")
    seed_packs(client)
    return client


@pytest.fixture
def admin(app: TestClient, oidc: FakeGitLabOIDC) -> TestClient:
    sign_in_as(app, oidc, "alice")
    return app


def _run(client: TestClient, fn: Callable[[Any], Any]) -> Any:
    state = client.app.state.z  # type: ignore[attr-defined]

    async def go() -> Any:
        return await fn(state)

    return client.portal.call(go)  # type: ignore[union-attr]


def _configure(admin: TestClient, targets: list[dict[str, Any]], smtp: dict[str, Any] | None = None) -> Any:
    resp = admin.put("/api/v1/admin/notifications", json={"targets": targets, "smtp": smtp}, headers=csrf(admin))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _mission(admin: TestClient, data_class: str = "C2") -> str:
    created = admin.post(
        "/api/v1/missions",
        json={"playbook": "summarize-file", "inputs": {"file": "n.md"}, "start": False, "data_class": data_class},
        headers=csrf(admin),
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def _emit(admin: TestClient, mission_id: str, event_type: str, payload: dict[str, Any]) -> None:
    async def go(state: Any) -> None:
        async with state.sessions() as db, db.begin():
            await events.append(db, mission_id, event_type, payload)

    _run(admin, go)


def _tick(admin: TestClient) -> dict[str, int]:
    return dict(_run(admin, notifications.tick))


def _retry_now(admin: TestClient) -> None:
    async def go(state: Any) -> None:
        async with state.sessions() as db, db.begin():
            for row in (await db.execute(select(NotificationOutbox))).scalars():
                row.next_at = row.created_at

    _run(admin, go)


PROPOSED = {
    "action_id": "act_1",
    "connector": "jira",
    "operation": "jira.issue.comment",
    "class": "write",
    "summary": "Comment <b>on</b> SEC-42 with the triage result",
}


def test_settings_take_references_only_and_report_delivery(admin: TestClient) -> None:
    bad = [
        [{"id": "ops", "kind": "slack", "events": ["approval_requested"], "url_ref": "https://hooks.slack.com/x"}],
        [{"id": "ops", "kind": "slack", "events": ["approval_requested"]}],
        [{"id": "ops", "kind": "webhook", "events": ["everything"], "url_ref": "env:X"}],
        [{"id": "mail", "kind": "email", "events": ["mission_failed"], "to": ["secops@example.internal"]}],
    ]
    for targets in bad:
        resp = admin.put("/api/v1/admin/notifications", json={"targets": targets}, headers=csrf(admin))
        assert resp.status_code == 422, targets
    saved = _configure(
        admin,
        [{"id": "ops", "kind": "slack", "events": ["approval_requested"], "url_ref": "env:Z_SLACK_HOOK"}],
    )
    assert saved["targets"][0]["url_ref"] == "env:Z_SLACK_HOOK" and "status" not in saved["targets"][0]
    changes = admin.get("/api/v1/audit", params={"action": "admin.notifications_changed"}).json()["items"]
    assert len(changes) == 1


def test_events_reach_matching_targets_with_content_up_to_their_class(
    admin: TestClient, receiver: Receiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("Z_HOOK_SLACK", f"{receiver.url}/slack")
    monkeypatch.setenv("Z_HOOK_TEAMS", f"{receiver.url}/teams")
    monkeypatch.setenv("Z_HOOK_MM", f"{receiver.url}/mm")
    _configure(
        admin,
        [
            {"id": "slack", "kind": "slack", "events": ["approval_requested"], "url_ref": "env:Z_HOOK_SLACK"},
            {
                "id": "teams",
                "kind": "teams",
                "events": ["approval_requested", "mission_succeeded"],
                "url_ref": "env:Z_HOOK_TEAMS",
                "content_up_to": "C3",
            },
            {"id": "mm", "kind": "mattermost", "events": ["question_asked"], "url_ref": "env:Z_HOOK_MM"},
        ],
    )
    mission = _mission(admin)  # C2
    _emit(admin, mission, "action_proposed", PROPOSED)
    report = _tick(admin)
    assert report["sent"] == 2
    slack = receiver.bodies("/slack")[0]["text"]
    assert "Approval needed" in slack and "Details are in Z (data class C2)" in slack and "SEC-42" not in slack
    assert "/review?action=act_1|Review in Z>" in slack
    card = receiver.bodies("/teams")[0]["attachments"][0]
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert "Comment <b>on</b> SEC-42" in json.dumps(card["content"]["body"])
    assert receiver.bodies("/mm") == []
    _emit(admin, mission, "status", {"entity": "mission", "from": "running", "to": "succeeded"})
    _tick(admin)
    assert len(receiver.bodies("/teams")) == 2 and len(receiver.bodies("/slack")) == 1
    state = admin.get("/api/v1/admin/notifications").json()["targets"]
    assert next(t for t in state if t["id"] == "teams")["status"].get("last_error") is None


def test_webhooks_are_signed_and_failures_retry_without_resending(
    admin: TestClient, receiver: Receiver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("Z_HOOK_A", f"{receiver.url}/a")
    monkeypatch.setenv("Z_HOOK_B", f"{receiver.url}/b")
    signing_key = "test-" + "signing-" + "key"  # fake; built at run time so no key-shaped literal is committed
    monkeypatch.setenv("Z_HOOK_SECRET", signing_key)
    events_ = ["needs_attention"]
    _configure(
        admin,
        [
            {
                "id": "a",
                "kind": "webhook",
                "events": events_,
                "url_ref": "env:Z_HOOK_A",
                "secret_ref": "env:Z_HOOK_SECRET",
            },
            {"id": "b", "kind": "webhook", "events": events_, "url_ref": "env:Z_HOOK_B"},
        ],
    )
    mission = _mission(admin, "C1")
    _emit(admin, mission, "status", {"entity": "job", "from": "running", "to": "needs_attention", "reason": "judge"})
    receiver.statuses = [200, 503]  # a accepts, b fails
    first = _tick(admin)
    assert first == {"sent": 1, "failed": 1, "rows": 1}
    _, headers, raw = next(r for r in receiver.requests if r[0] == "/a")
    expected = hmac.new(signing_key.encode(), headers["x-z-timestamp"].encode() + b"." + raw, hashlib.sha256)
    assert headers["x-z-signature"] == f"sha256={expected.hexdigest()}"
    body = json.loads(raw)
    assert body["kind"] == "needs_attention" and body["link"].endswith(f"/missions/{mission}")
    assert {"name": "Reason", "value": "judge"} in body["facts"]
    assert _tick(admin)["rows"] == 0  # backing off
    _retry_now(admin)
    second = _tick(admin)
    assert second == {"sent": 1, "failed": 0, "rows": 1}
    assert [p for p, _, _ in receiver.requests] == ["/a", "/b", "/b"]  # a got it once
    b = next(t for t in admin.get("/api/v1/admin/notifications").json()["targets"] if t["id"] == "b")
    assert b["status"].get("last_error") is None and b["status"]["last_sent_at"]


def test_the_outbox_is_written_with_the_event_and_rolled_back_with_it(admin: TestClient) -> None:
    mission = _mission(admin)

    async def failing(state: Any) -> None:
        async with state.sessions() as db, db.begin():
            await events.append(db, mission, "question", {"question_id": "q_1", "text": "Which project?"})
            await db.rollback()

    _run(admin, failing)

    async def count(state: Any) -> int:
        async with state.sessions() as db:
            return len(list((await db.execute(select(NotificationOutbox))).scalars()))

    assert _run(admin, count) == 0
    _emit(admin, mission, "question", {"question_id": "q_1", "text": "Which project?"})
    assert _run(admin, count) == 1


def test_budget_alerts_once_per_period(admin: TestClient, receiver: Receiver, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Z_HOOK", f"{receiver.url}/budget")
    _configure(admin, [{"id": "fin", "kind": "webhook", "events": ["budget_alert"], "url_ref": "env:Z_HOOK"}])
    created = admin.put(
        "/api/v1/budgets",
        json=[{"scope": "org", "limit": 1000, "period": "day", "alert_at": 0.8, "hard": False}],
        headers=csrf(admin),
    )
    assert created.status_code == 200, created.text

    async def used(db: Any, scope: str, period: str, at: Any) -> int:
        return 850

    from z_server import usage

    monkeypatch.setattr(usage, "used", used)
    monkeypatch.setattr(notifications, "_budget_checked", 0.0)
    _tick(admin)  # enqueues the alert
    _tick(admin)  # delivers it
    monkeypatch.setattr(notifications, "_budget_checked", 0.0)
    _tick(admin)
    alerts = receiver.bodies("/budget")
    assert len(alerts) == 1 and "org has used 850 of 1000 tokens this day" in alerts[0]["text"]


def test_test_notification_reports_failures(admin: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Z_HOOK_META", "http://169.254.169.254/latest")
    _configure(admin, [{"id": "x", "kind": "webhook", "events": ["mission_failed"], "url_ref": "env:Z_HOOK_META"}])
    resp = admin.post("/api/v1/admin/notifications:test", json={"target_id": "x"}, headers=csrf(admin))
    assert resp.status_code == 503 and "resolves to a" in resp.json()["detail"]
    missing = admin.post("/api/v1/admin/notifications:test", json={"target_id": "nope"}, headers=csrf(admin))
    assert missing.status_code == 404


def test_mission_rows_keep_team_and_class(admin: TestClient) -> None:
    mission = _mission(admin, "C1")
    _emit(admin, mission, "status", {"entity": "mission", "from": "running", "to": "failed", "reason": "error"})

    async def rows(state: Any) -> list[tuple[str, str]]:
        async with state.sessions() as db:
            m = await db.get(Mission, mission)
            assert m is not None
            return [(r.kind, r.data_class) for r in (await db.execute(select(NotificationOutbox))).scalars()]

    assert _run(admin, rows) == [("mission_failed", "C1")]
