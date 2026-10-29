"""Scheduler on a real Postgres: concurrency caps, crash and lease recovery with resume from checkpoint,
retries with backoff, deadlines, pause, and cleanup of ended runners."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from z_server.db.models import Job, Mission
from z_server.missions.service import utcnow
from z_server.scheduler import queue

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import base_config, csrf, seed_packs, sign_in_as

SUMMARY = {"title": "t", "bullets": ["a"], "source": "s"}


@pytest.fixture
def keys() -> RecordingKeyAdmin:
    return RecordingKeyAdmin()


@pytest.fixture
def executor() -> RecordingExecutor:
    return RecordingExecutor()


def make(
    make_client: Callable[..., TestClient],
    oidc: FakeGitLabOIDC,
    keys: RecordingKeyAdmin,
    tmp_path: Any,
    **executor_config: int,
) -> TestClient:
    config = base_config(oidc.issuer)
    config.setdefault("executor", {}).update(executor_config)
    app = make_client(config=config, llm_keys=keys, artifacts_dir=tmp_path / "artifacts")
    seed_packs(app)
    sign_in_as(app, oidc, "bob")
    return app


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, keys: RecordingKeyAdmin, tmp_path: Any
) -> TestClient:
    return make(make_client, oidc, keys, tmp_path)


def run(app: TestClient, fn: Callable[[Any], Awaitable[Any]]) -> Any:
    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> Any:
        async with state.sessions() as db, db.begin():
            return await fn(db)

    return app.portal.call(go)  # type: ignore[union-attr]


def tick(app: TestClient, executor: RecordingExecutor) -> dict[str, Any]:
    state = app.app.state.z  # type: ignore[attr-defined]
    return app.portal.call(queue.tick, state, executor, "test-worker")  # type: ignore[union-attr,no-any-return]


def mission(app: TestClient, **extra: Any) -> str:
    resp = app.post(
        "/api/v1/missions", json={"playbook": "summarize-file", "inputs": {"file": "f.md"}, **extra}, headers=csrf(app)
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def fanout_mission(app: TestClient, concurrency: int) -> str:
    plan = {
        "steps": [
            {"id": "list", "agent": "summarizer", "prompt": "List the items"},
            {
                "id": "each",
                "agent": "summarizer",
                "prompt": "Handle {{ item.name }}",
                "depends_on": ["list"],
                "fanout": {"over": "steps.list.output.items", "concurrency": concurrency},
            },
        ]
    }
    resp = app.post(
        "/api/v1/missions", json={"title": "Fan-out", "prompt": "Handle all", "plan": plan}, headers=csrf(app)
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def jobs(app: TestClient, mission_id: str, status: str | None = None) -> list[dict[str, Any]]:
    params = {"status": status} if status else {}
    return list(app.get(f"/api/v1/missions/{mission_id}/jobs", params=params).json()["items"])


def test_fanout_respects_step_concurrency(app: TestClient, executor: RecordingExecutor) -> None:
    mission_id = fanout_mission(app, concurrency=2)
    [first] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, first)
    runner.get_spec()
    runner.complete(output={"items": [{"name": f"host-{i}"} for i in range(5)]})
    assert len(jobs(app, mission_id, "queued")) == 5
    started = dispatch(app, executor)
    assert len(started) == 2 and dispatch(app, executor) == []
    runners = [ScriptedRunner.for_job(app, executor, j) for j in started]
    assert sorted(r.get_spec()["prompt"] for r in runners) == ["Handle host-0", "Handle host-1"]
    assert runners[0].complete(output={"ok": True}).status_code == 200
    assert len(dispatch(app, executor)) == 1


def test_global_cap(
    make_client: Callable[..., TestClient],
    oidc: FakeGitLabOIDC,
    keys: RecordingKeyAdmin,
    tmp_path: Any,
    executor: RecordingExecutor,
) -> None:
    app = make(make_client, oidc, keys, tmp_path, max_concurrency=1)
    mission(app)
    mission(app)
    assert len(dispatch(app, executor)) == 1
    assert dispatch(app, executor) == []


def test_runner_crash_resumes_from_checkpoint_with_backoff(app: TestClient, executor: RecordingExecutor) -> None:
    mission_id = mission(app)
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    assert runner.checkpoint(3, b"state-3").status_code == 200

    async def expire(db: Any) -> None:
        await db.execute(update(Job).where(Job.id == job_id).values(lease_expires_at=utcnow() - timedelta(seconds=1)))

    run(app, expire)
    report = tick(app, executor)
    assert report["requeued"] == [job_id] and report["dispatched"] == []  # backoff: not before 5 s
    [job] = jobs(app, mission_id)
    assert job["status"] == "queued" and job["attempt"] == 2 and job["resume_from_step"] == 3
    assert f"rec-{job_id}" in executor.stopped
    assert runner.call("GET", "/control").status_code == 401  # the dead runner's token is gone

    async def skip_backoff(db: Any) -> None:
        await db.execute(update(Job).where(Job.id == job_id).values(available_at=utcnow()))

    run(app, skip_backoff)
    assert tick(app, executor)["dispatched"] == [job_id]
    second = ScriptedRunner.for_job(app, executor, job_id)
    spec = second.get_spec()
    assert spec["attempt"] == 2 and spec["resume_from_step"] == 3
    import base64

    assert base64.b64decode(spec["checkpoint_b64"]) == b"state-3"
    second.complete(output=SUMMARY)
    assert app.get(f"/api/v1/missions/{mission_id}").json()["status"] == "succeeded"
    assert tick(app, executor)["reaped"] == [job_id]  # the ended runner is cleaned up once
    assert tick(app, executor)["reaped"] == []


def test_dead_runner_is_detected_and_fails_after_max_attempts(
    app: TestClient, executor: RecordingExecutor, keys: RecordingKeyAdmin
) -> None:
    mission_id = mission(app)
    for attempt in (1, 2, 3):

        async def ready(db: Any) -> None:
            await db.execute(update(Job).where(Job.mission_id == mission_id).values(available_at=utcnow()))

        run(app, ready)
        [job_id] = dispatch(app, executor)
        executor.alive.discard(f"rec-{job_id}")  # the container exited without completing
        report = tick(app, executor)
        assert (report["requeued"] if attempt < 3 else report["failed"]) == [job_id]
    [job] = jobs(app, mission_id)
    assert job["status"] == "failed" and job["error"]["class"] == "runner_lost"
    assert app.get(f"/api/v1/missions/{mission_id}").json()["status"] == "failed"
    assert keys.calls[-1][0] == "delete"


def test_runner_output_is_kept_redacted_when_it_dies(app: TestClient, executor: RecordingExecutor) -> None:
    mission_id = mission(app)
    [job_id] = dispatch(app, executor)
    executor.output[f"rec-{job_id}"] = (
        "Traceback: boom\nAuthorization: Bearer glpat-abcdefghij0123456789\n[exit code 1]"
    )
    executor.alive.discard(f"rec-{job_id}")
    assert tick(app, executor)["requeued"] == [job_id]
    [job] = jobs(app, mission_id)
    assert "last runner output" in job["error"]["message"] and "boom" in job["error"]["message"]
    assert "glpat-abcdefghij0123456789" not in job["error"]["message"]


def test_job_and_mission_deadlines(app: TestClient, executor: RecordingExecutor, keys: RecordingKeyAdmin) -> None:
    late_job = mission(app)
    [job_id] = dispatch(app, executor)

    async def past_job_deadline(db: Any) -> None:
        await db.execute(update(Job).where(Job.id == job_id).values(deadline_at=utcnow() - timedelta(seconds=1)))

    run(app, past_job_deadline)
    assert tick(app, executor)["timed_out"] == [job_id]
    assert jobs(app, late_job)[0]["status"] == "timed_out"
    assert app.get(f"/api/v1/missions/{late_job}").json()["status"] == "failed"

    late_mission = mission(app)

    async def past_mission_deadline(db: Any) -> None:
        await db.execute(
            update(Mission).where(Mission.id == late_mission).values(deadline_at=utcnow() - timedelta(seconds=1))
        )

    run(app, past_mission_deadline)
    tick(app, executor)
    assert app.get(f"/api/v1/missions/{late_mission}").json()["status"] == "timed_out"
    assert all(j["status"] == "cancelled" for j in jobs(app, late_mission))


def test_paused_missions_are_not_dispatched(app: TestClient, executor: RecordingExecutor) -> None:
    mission_id = mission(app)
    paused = app.post(f"/api/v1/missions/{mission_id}/pause", json={}, headers=csrf(app))
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    assert dispatch(app, executor) == []
    app.post(f"/api/v1/missions/{mission_id}/resume", json={}, headers=csrf(app))
    assert len(dispatch(app, executor)) == 1


def test_executor_failure_requeues(app: TestClient, keys: RecordingKeyAdmin) -> None:
    from z_server.executors import ExecutorError

    class Broken(RecordingExecutor):
        async def launch(self, job_id: str, env: dict[str, str]) -> str:
            raise ExecutorError("docker socket proxy unreachable")

    mission_id = mission(app)
    assert dispatch(app, Broken()) == []  # type: ignore[arg-type]
    [job] = jobs(app, mission_id)
    assert job["status"] == "queued" and job["attempt"] == 2


def test_all_mode_runs_the_scheduler_loop(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, keys: RecordingKeyAdmin, tmp_path: Any
) -> None:
    """z-server all: the loop ticks on its own. The Docker API is unreachable here, so the job is re-queued."""
    import time

    config = base_config(oidc.issuer)
    config["executor"]["docker"]["socket_proxy_url"] = "http://127.0.0.1:9"
    app = make_client(config=config, llm_keys=keys, artifacts_dir=tmp_path / "a", workers=True)
    seed_packs(app)
    sign_in_as(app, oidc, "bob")
    mission_id = mission(app)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        found = jobs(app, mission_id)
        if found and found[0]["attempt"] >= 2:
            break
        time.sleep(0.2)
    [job] = jobs(app, mission_id)
    assert job["attempt"] >= 2 and job["status"] == "queued"


def test_production_refuses_an_unpinned_runner_image() -> None:
    from z_server.settings import Settings, SettingsError

    settings = Settings(
        database_url="postgresql+psycopg://x/y", secret_key="s" * 40, env="production", runner_image="z-runner:latest"
    )
    with pytest.raises(SettingsError, match="pinned by digest"):
        settings.check()
