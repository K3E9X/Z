"""Concurrency on a real Postgres: one lock order (mission row, then job/action row, then the audit lock), no lost
update of the mission counters, a stale watchdog snapshot, the global stream cursor, ended missions."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from z_server import events
from z_server.api.routes_usage import _global
from z_server.audit import LOCK_KEY
from z_server.db.models import Job, Mission
from z_server.missions.service import utcnow
from z_server.scheduler import queue

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar


@pytest.fixture
def keys() -> RecordingKeyAdmin:
    return RecordingKeyAdmin()


@pytest.fixture
def executor() -> RecordingExecutor:
    return RecordingExecutor()


@pytest.fixture
def app(make_client: Callable[..., TestClient], keys: RecordingKeyAdmin, tmp_path: Any) -> TestClient:
    client = make_client(llm_keys=keys, artifacts_dir=tmp_path / "artifacts")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    return {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}


def as_user(app: TestClient, jars: dict[str, dict[str, str]], user: str) -> dict[str, str]:
    use_jar(app, jars[user])
    return csrf(app)


def run(app: TestClient, fn: Callable[[Any], Any]) -> Any:
    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> Any:
        async with state.sessions() as db, db.begin():
            return await fn(db)

    return app.portal.call(go)  # type: ignore[union-attr]


def dsn(app: TestClient) -> str:
    return str(app.app.state.z.settings.database_url).replace("postgresql+psycopg://", "postgresql://")  # type: ignore[attr-defined]


def fanout(app: TestClient, h: dict[str, str]) -> str:
    plan = {
        "steps": [
            {"id": "list", "agent": "summarizer", "prompt": "List"},
            {
                "id": "each",
                "agent": "summarizer",
                "prompt": "Handle {{ item.name }}",
                "depends_on": ["list"],
                "fanout": {"over": "steps.list.output.items", "concurrency": 2},
            },
        ]
    }
    resp = app.post("/api/v1/missions", json={"title": "F", "prompt": "F", "plan": plan}, headers=h)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# ------------------------------------------------------------------ 1. lost update of mission token counters


def test_concurrent_usage_events_keep_every_mission_token(
    app: TestClient, jars: dict[str, dict[str, str]], executor: RecordingExecutor
) -> None:
    h = as_user(app, jars, "bob")
    mission_id = fanout(app, h)
    [first] = dispatch(app, executor)
    r = ScriptedRunner.for_job(app, executor, first)
    r.get_spec()
    assert r.complete(output={"items": [{"name": "a"}, {"name": "b"}]}).status_code == 200
    started = dispatch(app, executor)
    assert len(started) == 2
    runners = [ScriptedRunner.for_job(app, executor, j) for j in started]
    for x in runners:
        x.get_spec()
    asgi = app.app
    per_call, calls = 10, 15

    async def burst() -> list[int]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), base_url="https://testserver") as c:
            reqs = []
            for _ in range(calls):
                for x in runners:
                    body = {
                        "events": [
                            {
                                "ts": utcnow().isoformat(),
                                "type": "usage",
                                "payload": {"model": "z-fast", "tokens_in": per_call, "tokens_out": 0},
                            }
                        ]
                    }
                    reqs.append(
                        c.post(
                            f"/api/v1/internal/jobs/{x.job_id}/events",
                            json=body,
                            headers={"Authorization": f"Bearer {x.session}"},
                        )
                    )
            return [resp.status_code for resp in await asyncio.gather(*reqs)]

    codes = app.portal.call(burst)  # type: ignore[union-attr]
    assert set(codes) == {200}, codes

    async def totals(db: Any) -> tuple[int, int]:
        m = await db.get(Mission, mission_id)
        jobs = (await db.execute(select(Job).where(Job.id.in_(started)))).scalars().all()
        return m.tokens_in, sum(j.tokens_in for j in jobs)

    mission_tokens, job_tokens = run(app, totals)
    assert job_tokens == per_call * calls * 2
    assert mission_tokens == job_tokens, f"mission counted {mission_tokens} of {job_tokens} tokens"


# ------------------------------------------------------------------ 2. lock-order deadlocks


def expose(app: TestClient, jars: dict[str, dict[str, str]], connector: str) -> None:
    h = as_user(app, jars, "alice")
    ops = app.get(f"/api/v1/connectors/{connector}/operations").json()
    decisions = [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]
    resp = app.post(f"/api/v1/connectors/{connector}/approve-exposure", json={"operations": decisions}, headers=h)
    assert resp.status_code == 200, resp.text


def pending_action(app: TestClient, jars: dict[str, dict[str, str]], executor: RecordingExecutor) -> tuple[str, str]:
    expose(app, jars, "jira")
    h = as_user(app, jars, "bob")
    writes = ["jira.issues.create"]
    plan = {"steps": [{"id": "act", "agent": "ticket-manager", "prompt": "Open", "writes": writes}], "writes": writes}
    created = app.post(
        "/api/v1/missions",
        json={"title": "T", "prompt": "Open", "plan": plan, "connectors": ["jira", "logos"]},
        headers=h,
    )
    assert created.status_code == 201, created.text
    mission_id = created.json()["id"]
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    body = {
        "connector": "jira",
        "operation": "jira.issues.create",
        "summary": "Create",
        "payload": {"project": "SEC", "summary": "Patch", "description": "x"},
    }
    resp = runner.call("POST", "/actions", json=body)
    assert resp.status_code == 201, resp.text
    return mission_id, resp.json()["id"]


def test_approve_takes_the_mission_row_before_the_audit_lock(
    app: TestClient, jars: dict[str, dict[str, str]], executor: RecordingExecutor
) -> None:
    """The gateway's _finish, the executor's _finish, cancel and propose take the mission row, then the audit
    lock: approving must take them in the same order (it used to take the audit lock first)."""
    mission_id, action_id = pending_action(app, jars, executor)
    c = as_user(app, jars, "carol")
    with psycopg.connect(dsn(app)) as conn:
        conn.execute("SELECT id FROM missions WHERE id = %s FOR UPDATE", (mission_id,))  # like Gateway._finish
        out: dict[str, Any] = {}
        t = threading.Thread(
            target=lambda: out.setdefault("r", app.post(f"/api/v1/actions/{action_id}/approve", json={}, headers=c))
        )
        t.start()
        time.sleep(1.5)  # approve waits for the mission row, holding nothing the other side needs
        err = None
        try:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))  # like audit.record
        except psycopg.errors.DeadlockDetected as exc:
            err = exc
        conn.rollback()
        t.join(30)
    resp = out["r"]
    assert err is None and resp.status_code == 200, f"deadlock: other side err={err!r}, approve={resp.status_code}"


def test_runner_requests_take_the_mission_row_before_the_job_row(
    app: TestClient, jars: dict[str, dict[str, str]], executor: RecordingExecutor
) -> None:
    """Cancel and pause lock the mission row, then update its jobs; a runner request (internal._job_ctx) must
    not hold its job row while it waits for the mission row (it used to lock the job first)."""
    h = as_user(app, jars, "bob")
    mission_id = fanout(app, h)
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    event = {"ts": utcnow().isoformat(), "type": "thought", "payload": {"text": "working"}}
    with psycopg.connect(dsn(app)) as conn:
        conn.execute("SELECT id FROM missions WHERE id = %s FOR UPDATE", (mission_id,))  # like cancel
        out: dict[str, Any] = {}
        t = threading.Thread(
            target=lambda: out.setdefault("r", runner.call("POST", "/events", json={"events": [event]}))
        )
        t.start()
        time.sleep(1.5)  # the runner request now waits for the mission row
        err = None
        try:
            conn.execute("UPDATE jobs SET control_state = 'cancel' WHERE id = %s", (job_id,))  # like stop_jobs
        except psycopg.errors.DeadlockDetected as exc:
            err = exc
        conn.commit()
        t.join(30)
    resp = out["r"]
    assert err is None and resp.status_code == 200, f"deadlock: err={err!r}, runner={resp.status_code} {resp.text}"
    assert resp.json()["state"] == "cancel"  # read after the lock: it sees what the other side committed


# ------------------------------------------------------------------ 3. stale watchdog snapshot (two replicas)


def test_stale_watchdog_snapshot_leaves_a_redispatched_job_alone(
    app: TestClient, jars: dict[str, dict[str, str]], executor: RecordingExecutor
) -> None:
    h = as_user(app, jars, "bob")
    fanout(app, h)
    [job_id] = dispatch(app, executor)

    async def expire(db: Any) -> None:
        await db.execute(update(Job).where(Job.id == job_id).values(lease_expires_at=utcnow() - timedelta(seconds=1)))

    run(app, expire)
    state = app.app.state.z  # type: ignore[attr-defined]
    # replica A's watchdog requeues the dead job; replica B read the same "running, lease expired" snapshot
    seen = run(app, lambda db: db.get(Job, job_id)).attempt
    requeued = app.portal.call(  # type: ignore[union-attr]
        lambda: queue.requeue_or_fail(state, job_id, "lease expired", seen_attempt=seen)
    )
    assert requeued == "requeued"

    async def now_available(db: Any) -> None:
        await db.execute(update(Job).where(Job.id == job_id).values(available_at=utcnow()))

    run(app, now_available)
    assert dispatch(app, executor) == [job_id]  # replica A dispatches a new runner
    # replica B acts on its stale snapshot
    outcome = app.portal.call(  # type: ignore[union-attr]
        lambda: queue.requeue_or_fail(state, job_id, "lease expired", seen_attempt=seen)
    )

    async def read(db: Any) -> Job:
        return await db.get(Job, job_id)

    job = run(app, read)
    assert outcome == "skipped" and job.status == "running", (
        f"{outcome}: job {job.status} attempt {job.attempt} executor_ref {job.executor_ref}; "
        f"live runner refs {sorted(executor.alive)}"
    )


# ------------------------------------------------------------------ 4. global stream cursor


def test_global_stream_delivers_an_event_stamped_earlier(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, tmp_path: Any
) -> None:
    app = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "a")
    seed_packs(app)
    sign_in_as(app, oidc, "bob")
    plan = {"steps": [{"id": "s", "agent": "summarizer", "prompt": "x"}]}
    resp = app.post(
        "/api/v1/missions", json={"title": "A", "prompt": "A", "plan": plan, "start": False}, headers=csrf(app)
    )
    assert resp.status_code == 201, resp.text
    mission_id = resp.json()["id"]
    state = app.app.state.z  # type: ignore[attr-defined]

    async def scenario() -> list[str]:
        async def add(text: str, ts: Any) -> None:
            async with state.sessions() as db, db.begin():
                await events.append(db, mission_id, "thought", {"text": text}, ts=ts)

        gen = _global(state, None, "unused", utcnow() - timedelta(seconds=5))
        seen: list[str] = []

        async def pull_until(marker: str) -> None:
            while marker not in "".join(seen):
                try:
                    seen.append(await asyncio.wait_for(gen.__anext__(), 20))
                except TimeoutError:
                    return

        await pull_until("retry:")
        t0 = utcnow()
        await add("first", t0)
        await pull_until('"first"')
        # a runner batch posted now with the ts the runner stamped earlier (or a transaction that committed late)
        await add("late", t0 - timedelta(seconds=2))
        await add("third", t0 + timedelta(seconds=1))
        await pull_until('"third"')
        await pull_until('"late"')
        await gen.aclose()
        return seen

    seen = app.portal.call(scenario)  # type: ignore[union-attr]
    text = "".join(seen)
    assert "first" in text and "third" in text, seen
    assert "late" in text, f"the global stream never delivered the 'late' event: {seen}"
    assert text.count('"first"') == 1  # and nothing twice


# ------------------------------------------------------------------ 5. ended missions leave nothing to apply


def test_cancel_closes_pending_actions(
    app: TestClient, jars: dict[str, dict[str, str]], executor: RecordingExecutor
) -> None:
    mission_id, action_id = pending_action(app, jars, executor)
    h = as_user(app, jars, "bob")
    assert app.post(f"/api/v1/missions/{mission_id}/cancel", json={}, headers=h).status_code == 200
    c = as_user(app, jars, "carol")
    action = app.get(f"/api/v1/actions/{action_id}").json()
    assert action["status"] == "rejected" and "mission ended" in action["result"]["reason"], action
    refused = app.post(f"/api/v1/actions/{action_id}/approve", json={}, headers=c)
    assert refused.status_code == 409, refused.text


def test_executor_recovery_claims_a_stale_action_once(
    app: TestClient, jars: dict[str, dict[str, str]], executor: RecordingExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    from z_server.db.models import ProposedAction
    from z_server.gateway import executor as action_executor

    _, action_id = pending_action(app, jars, executor)

    async def stale(db: Any) -> None:
        await db.execute(
            update(ProposedAction)
            .where(ProposedAction.id == action_id)
            .values(status="applying", updated_at=utcnow() - timedelta(minutes=30))
        )

    run(app, stale)
    applied: list[str] = []

    async def fake_apply(state: Any, gateway: Any, aid: str, *, recovering: bool = False) -> str:
        applied.append(aid)
        return "applied"

    monkeypatch.setattr(action_executor, "apply", fake_apply)
    state = app.app.state.z  # type: ignore[attr-defined]
    assert app.portal.call(action_executor.recover, state, None) == [action_id]  # type: ignore[union-attr]
    assert app.portal.call(action_executor.recover, state, None) == []  # type: ignore[union-attr]
    assert applied == [action_id]


# ------------------------------------------------------------------ 6. admin guardrail changes reach jobs


def test_admin_guardrail_change_caps_the_next_job(
    app: TestClient, jars: dict[str, dict[str, str]], executor: RecordingExecutor
) -> None:
    a = as_user(app, jars, "alice")
    changed = app.put(
        "/api/v1/settings/guardrails",
        json={"max_steps_per_job": {"value": 7, "locked": False}, "max_job_minutes": {"value": 3, "locked": False}},
        headers=a,
    )
    assert changed.status_code == 200, changed.text
    fanout(app, as_user(app, jars, "bob"))
    [job_id] = dispatch(app, executor)
    spec = ScriptedRunner.for_job(app, executor, job_id).get_spec()
    assert spec["limits"]["max_steps"] == 7 and spec["limits"]["timeout_s"] <= 180, spec["limits"]
