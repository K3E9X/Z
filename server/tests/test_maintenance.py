"""Maintenance mode (getMaintenance / setMaintenance): no mission or job starts while it is on, running jobs finish,
and every signed-in user sees the message."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

MESSAGE = "Upgrading the cluster until 14:00"
PLAN = {"steps": [{"id": "main", "agent": "ticket-manager", "prompt": "Look around"}]}


@pytest.fixture
def app(make_client: Callable[..., TestClient], tmp_path: Any) -> TestClient:
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "artifacts")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    return {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "dave")}


def as_user(app: TestClient, jars: dict[str, dict[str, str]], user: str) -> dict[str, str]:
    use_jar(app, jars[user])
    return csrf(app)


def run_db(app: TestClient, fn: Callable[[Any], Any]) -> Any:
    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> Any:
        async with state.sessions() as db, db.begin():
            return await fn(db)

    return app.portal.call(go)  # type: ignore[union-attr]


def maintenance(app: TestClient, jars: dict[str, dict[str, str]], enabled: bool, message: str | None = None) -> Any:
    h = as_user(app, jars, "alice")
    body: dict[str, Any] = {"enabled": enabled}
    if message is not None:
        body["message"] = message
    resp = app.put("/api/v1/admin/maintenance", json=body, headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()


def mission(app: TestClient, h: dict[str, str], start: bool = True) -> Any:
    return app.post(
        "/api/v1/missions", json={"title": "Look", "prompt": "Look", "plan": PLAN, "start": start}, headers=h
    )


def test_maintenance_switch_banner_and_audit(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    h = as_user(app, jars, "alice")
    assert app.get("/api/v1/admin/maintenance").json() == {"enabled": False, "message": None, "since": None, "by": None}
    h = as_user(app, jars, "bob")
    assert app.put("/api/v1/admin/maintenance", json={"enabled": True}, headers=h).status_code == 403
    assert not [b for b in app.get("/api/v1/me").json()["banners"] if b["kind"] == "maintenance"]

    on = maintenance(app, jars, True, MESSAGE)
    assert on["enabled"] is True and on["message"] == MESSAGE and on["by"] == "alice" and on["since"]
    assert maintenance(app, jars, True, MESSAGE)["since"] == on["since"]  # still the same maintenance window
    for user in ("bob", "dave", "alice"):
        as_user(app, jars, user)
        assert {"kind": "maintenance", "text": MESSAGE} in app.get("/api/v1/me").json()["banners"], user

    off = maintenance(app, jars, False)
    assert off == {"enabled": False, "message": None, "since": None, "by": None}
    as_user(app, jars, "bob")
    assert not [b for b in app.get("/api/v1/me").json()["banners"] if b["kind"] == "maintenance"]
    as_user(app, jars, "alice")
    actions = [e["action"] for e in app.get("/api/v1/audit", params={"action": "admin.maintenance"}).json()["items"]]
    assert actions[:2] == ["admin.maintenance_off", "admin.maintenance_on"]
    too_long = app.put("/api/v1/admin/maintenance", json={"enabled": True, "message": "x" * 501}, headers=csrf(app))
    assert too_long.status_code == 422


def test_nothing_starts_while_on_and_running_jobs_finish(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    executor = RecordingExecutor()
    h = as_user(app, jars, "bob")
    running = mission(app, h).json()["id"]
    queued = mission(app, h).json()["id"]
    jobs = {m: app.get(f"/api/v1/missions/{m}/jobs").json()["items"][0]["id"] for m in (running, queued)}

    # the first job runs before maintenance; the second one is still queued when it starts
    async def hold(db: Any) -> None:
        from z_server.db.models import Job

        await db.execute(
            update(Job).where(Job.id == jobs[queued]).values(available_at=datetime.now(UTC) + timedelta(hours=1))
        )

    run_db(app, hold)
    assert dispatch(app, executor) == [jobs[running]]
    runner = ScriptedRunner.for_job(app, executor, jobs[running])
    runner.get_spec()

    maintenance(app, jars, True, MESSAGE)
    h = as_user(app, jars, "bob")
    refused = mission(app, h)
    assert refused.status_code == 503, refused.text
    assert refused.json()["type"] == "urn:z:problem:unavailable" and MESSAGE in refused.json()["detail"]
    draft = mission(app, h, start=False)
    assert draft.status_code == 201 and draft.json()["status"] == "draft"  # preparing a mission is fine
    started = app.post(f"/api/v1/missions/{draft.json()['id']}/start", json={}, headers=h)
    assert started.status_code == 503 and MESSAGE in started.json()["detail"]
    run = app.post("/api/v1/playbooks/summarize-file/run", json={"inputs": {"file": "notes.md"}}, headers=h)
    assert run.status_code == 503, run.text
    titles = [m["title"] for m in app.get("/api/v1/missions").json()["items"]]
    assert len(titles) == 3  # the refused starts left nothing behind

    async def release(db: Any) -> None:
        from z_server.db.models import Job

        await db.execute(update(Job).where(Job.id == jobs[queued]).values(available_at=datetime.now(UTC)))

    run_db(app, release)
    assert dispatch(app, executor) == []  # the scheduler claims nothing
    # the running job goes on and finishes
    assert runner.control()["state"] == "run"
    done = runner.complete("failed", error={"class": "tool", "message": "boom"})
    assert done.status_code == 200, done.text
    retry = app.post(f"/api/v1/jobs/{jobs[running]}/retry", json={}, headers=as_user(app, jars, "bob"))
    assert retry.status_code == 503 and MESSAGE in retry.json()["detail"]

    maintenance(app, jars, False)
    assert dispatch(app, executor) == [jobs[queued]]
    assert (
        app.post(f"/api/v1/jobs/{jobs[running]}/retry", json={}, headers=as_user(app, jars, "bob")).status_code == 200
    )


def sign(secret: str, body: bytes) -> dict[str, str]:
    stamp = str(int(time.time()))
    mac = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Z-Timestamp": stamp, "X-Z-Signature": f"sha256={mac}", "Content-Type": "application/json"}


def test_triggers_start_nothing_while_on(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    from z_server.db.models import Trigger
    from z_server.workers.intake import fire_schedules

    h = as_user(app, jars, "alice")
    hook = app.post(
        "/api/v1/triggers",
        json={"kind": "webhook", "name": "Inbox", "playbook": "summarize-file", "inputs": {"file": "{{ event.file }}"}},
        headers=h,
    ).json()
    schedule = app.post(
        "/api/v1/triggers",
        json={
            "kind": "schedule",
            "name": "Hourly",
            "config": {"cron": "0 * * * *"},
            "playbook": "summarize-file",
            "inputs": {"file": "r.md"},
        },
        headers=h,
    ).json()
    due_at = datetime.now(UTC) - timedelta(minutes=1)

    async def due(db: Any) -> None:
        await db.execute(update(Trigger).where(Trigger.id == schedule["id"]).values(next_fire_at=due_at))

    run_db(app, due)
    maintenance(app, jars, True, MESSAGE)
    state = app.app.state.z  # type: ignore[attr-defined]
    assert app.portal.call(fire_schedules, state) == []  # type: ignore[union-attr]

    async def next_fire(db: Any) -> Any:
        return (await db.execute(select(Trigger.next_fire_at).where(Trigger.id == schedule["id"]))).scalar_one()

    assert run_db(app, next_fire) == due_at  # it fires once maintenance ends

    body = json.dumps({"file": "notes.md"}).encode()
    app.cookies.clear()
    received = app.post(f"/api/v1/webhooks/{hook['id']}", content=body, headers=sign(hook["secret"], body))
    assert received.status_code == 202, received.text
    assert received.json()["task_id"] and received.json()["mission_id"] is None
    as_user(app, jars, "alice")
    assert app.get("/api/v1/missions").json()["items"] == []

    maintenance(app, jars, False)
    assert len(app.portal.call(fire_schedules, state)) == 1  # type: ignore[union-attr]
