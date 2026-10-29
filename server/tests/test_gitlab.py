"""GitLab: git_clone workspaces through the clone helper, and merge requests, issues and notes through the
executor (real git over HTTP against the fake GitLab's bare repositories)."""

from __future__ import annotations

import io
import tarfile
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

PATCH = (
    "diff --git a/cleanup.sh b/cleanup.sh\n"
    "--- a/cleanup.sh\n"
    "+++ b/cleanup.sh\n"
    "@@ -1,2 +1,2 @@\n"
    " #!/bin/sh\n"
    "-find /var/tmp -mtime +7 -delete\n"
    "+find /var/tmp -mtime +14 -delete\n"
)


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], tools: FakeTools, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> TestClient:
    tools.reset()
    monkeypatch.setenv("GITLAB_URL", tools.url_for("gitlab"))
    monkeypatch.setenv("GITLAB_TOKEN_RO", tools.token("gitlab", "read"))
    monkeypatch.setenv("GITLAB_TOKEN_RW", tools.token("gitlab", "write"))
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "artifacts")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}
    use_jar(app, jars["alice"])
    ops = app.get("/api/v1/connectors/gitlab/operations").json()
    resp = app.post(
        "/api/v1/connectors/gitlab/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(app),
    )
    assert resp.status_code == 200, resp.text
    return jars


def start(app: TestClient, jars: dict[str, dict[str, str]], step: dict[str, Any]) -> tuple[str, ScriptedRunner]:
    use_jar(app, jars["bob"])
    plan = {
        "steps": [{"id": "work", "agent": "dev-engineer", "prompt": "Work", **step}],
        "writes": step.get("writes", []),
    }
    created = app.post(
        "/api/v1/missions",
        json={"title": "Dev", "prompt": "Dev", "plan": plan, "connectors": ["gitlab"]},
        headers=csrf(app),
    )
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    return str(created.json()["id"]), runner


def executor_tick(app: TestClient) -> dict[str, list[str]]:
    from z_server.gateway import executor

    state = app.app.state.z  # type: ignore[attr-defined]
    return app.portal.call(executor.tick, state, state.gateway)  # type: ignore[union-attr,no-any-return]


def approve(
    app: TestClient, jars: dict[str, dict[str, str]], runner: ScriptedRunner, operation: str, payload: dict[str, Any]
) -> str:
    proposed = runner.call(
        "POST",
        "/actions",
        json={"connector": "gitlab", "operation": operation, "summary": "change", "payload": payload},
    )
    assert proposed.status_code == 201, proposed.text
    action_id = str(proposed.json()["id"])
    use_jar(app, jars["carol"])
    approved = app.post(f"/api/v1/actions/{action_id}/approve", json={}, headers=csrf(app))
    assert approved.status_code == 200 and approved.json()["status"] == "approved", approved.text
    return action_id


def test_git_clone_workspace_is_staged_without_token_and_judged(
    app: TestClient, jars: dict[str, dict[str, str]]
) -> None:
    _, runner = start(app, jars, {"workspace": "git_clone", "repo": "infra/ops-scripts"})
    manifest = runner.call("GET", "/workspace/manifest").json()
    assert manifest["repo"] == "gitlab:infra/ops-scripts" and len(manifest["commit"]) == 40
    by_path = {f["path"]: f for f in manifest["files"]}
    assert by_path["cleanup.sh"]["judge"] == "clean" and by_path["README.md"]["judge"] == "blocked"
    resp = runner.call("GET", "/workspace")
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        names = tar.getnames()
        assert "cleanup.sh" in names and "README.md" not in names and not any(n.startswith(".git") for n in names)
        blob = b"".join(tar.extractfile(m).read() for m in tar.getmembers() if m.isfile())  # type: ignore[union-attr]
    assert b"ro-gitlab" not in blob and b"rw-gitlab" not in blob
    assert runner.control()["state"] == "pause"  # a human looks at the injected README first


def test_merge_request_from_the_agent_patch(app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools) -> None:
    _, runner = start(app, jars, {"writes": ["gitlab.merge_requests.create"]})
    art = runner.call(
        "POST",
        "/artifacts",
        data={"kind": "patch", "name": "changes.patch"},
        files={"file": ("changes.patch", PATCH.encode(), "text/x-diff")},
    )
    assert art.status_code == 201, art.text
    payload = {
        "project": "infra/ops-scripts",
        "source_branch": "z/retention-14d",
        "target_branch": "main",
        "title": "Keep tmp files 14 days",
        "patch_artifact": art.json()["id"],
    }
    action_id = approve(app, jars, runner, "gitlab.merge_requests.create", payload)
    preview = app.get(f"/api/v1/actions/{action_id}").json()["preview"]
    assert preview["kind"] == "diff" and "+14" in preview["diff"] and preview["files"][0]["path"] == "cleanup.sh"
    report = executor_tick(app)
    assert report["applied"] == [action_id], app.get(f"/api/v1/actions/{action_id}").json()["result"]
    result = app.get(f"/api/v1/actions/{action_id}").json()["result"]
    assert result["iid"] >= 1 and len(result["commit"]) == 40
    [mr] = tools.writes("gitlab.merge_requests.create")
    assert mr["token_kind"] == "write" and mr["data"]["sha"] == result["commit"]
    with tools.client() as c:
        raw = c.get(
            "/gitlab/api/v4/projects/infra%2Fops-scripts/repository/files/cleanup.sh/raw",
            params={"ref": "z/retention-14d"},
            headers={"Authorization": "Bearer ro-gitlab"},
        )
    assert "-mtime +14" in raw.text

    def crash(db: Any) -> Any:
        from z_server.db.models import ProposedAction

        return db.execute(
            update(ProposedAction)
            .where(ProposedAction.id == action_id)
            .values(status="applying", updated_at=ProposedAction.updated_at - timedelta(hours=1))
        )

    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> None:
        async with state.sessions() as db, db.begin():
            await crash(db)

    app.portal.call(go)  # type: ignore[union-attr]
    assert executor_tick(app)["recovered"] == [action_id]
    assert len(tools.writes("gitlab.merge_requests.create")) == 1
    assert app.get(f"/api/v1/actions/{action_id}").json()["result"]["recovered"] is True


def test_patches_escaping_the_repository_are_refused(
    app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools
) -> None:
    _, runner = start(app, jars, {"writes": ["gitlab.merge_requests.create"]})
    evil = "diff --git a/../../etc/cron.d/x b/../../etc/cron.d/x\nnew file mode 100644\n--- /dev/null\n+++ b/../../etc/cron.d/x\n@@ -0,0 +1 @@\n+* * * * * root sh\n"
    art = runner.call(
        "POST",
        "/artifacts",
        data={"kind": "patch", "name": "evil.patch"},
        files={"file": ("evil.patch", evil.encode(), "text/x-diff")},
    ).json()
    action_id = approve(
        app,
        jars,
        runner,
        "gitlab.merge_requests.create",
        {
            "project": "infra/ops-scripts",
            "source_branch": "z/evil",
            "target_branch": "main",
            "title": "x",
            "patch_artifact": art["id"],
        },
    )
    report = executor_tick(app)
    assert report["failed"] == [action_id], (report, app.get(f"/api/v1/actions/{action_id}").json())
    assert "forbidden path" in app.get(f"/api/v1/actions/{action_id}").json()["result"]["error"]
    assert tools.writes("gitlab.merge_requests.create") == []


def test_issue_with_assignee_and_note(app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools) -> None:
    _, runner = start(app, jars, {"writes": ["gitlab.issues.create", "gitlab.notes.create"]})
    issue_id = approve(
        app,
        jars,
        runner,
        "gitlab.issues.create",
        {
            "project": "infra/ops-scripts",
            "title": "Rotate keys",
            "description": "Before Friday.",
            "labels": ["security"],
            "assignee": "alice",
        },
    )
    assert executor_tick(app)["applied"] == [issue_id]
    issue = app.get(f"/api/v1/actions/{issue_id}").json()["result"]
    note_id = approve(
        app,
        jars,
        runner,
        "gitlab.notes.create",
        {"project": "infra/ops-scripts", "issue_iid": issue["iid"], "body": "Assigned by Z."},
    )
    assert executor_tick(app)["applied"] == [note_id]
    [note] = tools.writes("gitlab.notes.create")
    assert note["data"]["body"].startswith("Assigned by Z.") and f"z-action:{note_id}" in note["data"]["body"]
