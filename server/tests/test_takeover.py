"""Take-over end to end (ADR 0019): pause, browse the rebuilt workspace, edit, restart from the edited
checkpoint with a fresh runner, the agent told what changed; read-only sessions, one person at a time, paths
kept inside the workspace."""

from __future__ import annotations

import base64
import io
import json
import tarfile
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from z_common.checkpoint import Checkpoint, delta_files

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

NOTES = b"# Notes\nWe met on Monday.\n"


@pytest.fixture
def app(make_client: Callable[..., TestClient], tmp_path: Any) -> TestClient:
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "artifacts")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    return {u: sign_in_as(app, oidc, u) for u in ("bob", "carol")}


def as_user(app: TestClient, jars: dict[str, dict[str, str]], user: str) -> dict[str, str]:
    use_jar(app, jars[user])
    return csrf(app)


def delta(files: dict[str, bytes], deleted: list[str] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        meta = json.dumps({"deleted": deleted or []}).encode()
        info = tarfile.TarInfo(".z-delta.json")
        info.size = len(meta)
        tar.addfile(info, io.BytesIO(meta))
    return buf.getvalue()


def started(app: TestClient, h: dict[str, str], executor: RecordingExecutor) -> tuple[str, str, ScriptedRunner]:
    created = app.post(
        "/api/v1/missions",
        json={"playbook": "summarize-file", "inputs": {"file": "notes.md"}, "start": False},
        headers=h,
    )
    mission_id = created.json()["id"]
    up = app.post(
        f"/api/v1/missions/{mission_id}/files", files={"file": ("notes.md", NOTES, "text/markdown")}, headers=h
    )
    assert up.status_code == 201, up.text
    assert app.post(f"/api/v1/missions/{mission_id}/start", json={}, headers=h).status_code == 200
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    return mission_id, job_id, runner


def tick(app: TestClient, executor: RecordingExecutor) -> dict[str, Any]:
    from z_server.scheduler.queue import tick as run

    state = app.app.state.z  # type: ignore[attr-defined]
    return app.portal.call(run, state, executor, "test-worker")  # type: ignore[union-attr,no-any-return]


def test_take_over_edit_and_restart_from_the_edited_checkpoint(
    app: TestClient, jars: dict[str, dict[str, str]]
) -> None:
    executor = RecordingExecutor()
    h = as_user(app, jars, "bob")
    mission_id, job_id, runner = started(app, h, executor)
    ckpt = Checkpoint(backend=b"backend-state", runner={"steps": 2}, workspace=delta({"draft.md": b"first draft"}))
    assert runner.checkpoint(2, ckpt.pack()).status_code == 200
    base = f"/api/v1/missions/{mission_id}/take-over/{job_id}"

    opened = app.post(
        f"/api/v1/missions/{mission_id}/take-over", json={"job_id": job_id, "mode": "read_write"}, headers=h
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["terminal_url"].endswith(f"/missions/{mission_id}/take-over/{job_id}")
    assert runner.control()["state"] == "pause"  # the parked runner stops after its checkpoint

    files = {f["path"]: f for f in app.get(f"{base}/files").json()["files"]}
    assert files["notes.md"]["origin"] == "staged" and files["draft.md"]["origin"] == "agent"
    read = app.get(f"{base}/file", params={"path": "draft.md"}).json()
    assert read == {"path": "draft.md", "encoding": "utf-8", "content": "first draft", "size": 11, "origin": "agent"}
    assert app.get(f"{base}/file", params={"path": "notes.md"}).json()["content"] == NOTES.decode()

    put = app.put(f"{base}/file", json={"path": "notes.md", "content": "# Notes\nWe met on Tuesday.\n"}, headers=h)
    assert put.status_code == 200 and put.json()["origin"] == "human", put.text
    assert app.delete(f"{base}/file", params={"path": "draft.md"}, headers=h).status_code == 204
    for bad in ("../escape.txt", "/etc/passwd", ".z-delta.json", "a/../../b"):
        refused = app.put(f"{base}/file", json={"path": bad, "content": "x"}, headers=h)
        assert refused.status_code == 422, (bad, refused.text)
    view = {f["path"]: f for f in app.get(f"{base}/files").json()["files"]}
    assert view["notes.md"]["origin"] == "human" and view["draft.md"]["deleted"] is True

    h_carol = as_user(app, jars, "carol")  # someone else cannot read or take it over meanwhile
    assert app.get(f"{base}/files").status_code in {403, 404}
    assert app.post(
        f"/api/v1/missions/{mission_id}/take-over", json={"job_id": job_id}, headers=h_carol
    ).status_code in {403, 404, 409}

    h = as_user(app, jars, "bob")
    finished = app.post(f"{base}/finish", json={"resume": True}, headers=h)
    assert finished.status_code == 200, finished.text
    assert finished.json() == {"job_id": job_id, "edited": ["draft.md", "notes.md"], "restarted": True, "resumed": True}
    assert runner.call("GET", "/control").status_code == 401  # the parked runner is locked out at once

    report = tick(app, executor)
    assert job_id in report["reaped"] and job_id in report["dispatched"]  # old runner stopped, a new one started
    assert executor.stopped == [f"rec-{job_id}"]
    fresh = ScriptedRunner.for_job(app, executor, job_id)
    spec = fresh.get_spec()
    assert spec["resume_from_step"] == 2
    restored = Checkpoint.unpack(base64.b64decode(spec["checkpoint_b64"]))
    files_after, deleted = delta_files(restored.workspace)
    assert files_after == {"notes.md": b"# Notes\nWe met on Tuesday.\n"} and deleted == ["draft.md"]
    assert restored.backend == b"backend-state" and restored.runner == {"steps": 2}
    messages = fresh.control()["messages"]
    assert any("Take-over" in m["text"] and "notes.md" in m["text"] and "draft.md" in m["text"] for m in messages)
    job = app.get(f"/api/v1/jobs/{job_id}").json()
    assert job["attempt"] == 1  # a take-over is not a failure
    assert app.get(f"{base}/files").status_code == 409  # the session ended


def test_read_only_take_over_resumes_without_a_restart(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    executor = RecordingExecutor()
    h = as_user(app, jars, "bob")
    mission_id, job_id, runner = started(app, h, executor)
    base = f"/api/v1/missions/{mission_id}/take-over/{job_id}"
    refused = app.post(
        f"/api/v1/missions/{mission_id}/take-over", json={"job_id": job_id, "mode": "read_write"}, headers=h
    )
    assert refused.status_code == 409 and "has not finished a step" in refused.text
    assert app.post(f"/api/v1/missions/{mission_id}/take-over", json={"job_id": job_id}, headers=h).status_code == 200
    assert [f["path"] for f in app.get(f"{base}/files").json()["files"]] == ["notes.md"]
    assert app.put(f"{base}/file", json={"path": "notes.md", "content": "x"}, headers=h).status_code == 403
    done = app.post(f"{base}/finish", json={}, headers=h).json()
    assert done == {"job_id": job_id, "edited": [], "restarted": False, "resumed": True}
    assert runner.control()["state"] == "run"  # the same runner carries on
