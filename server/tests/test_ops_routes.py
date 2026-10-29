"""Usage, budgets, providers, inference monitor, analytics, global stream; triggers, webhooks, schedules, Jira
polling and the tasks board."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_prometheus import FakePrometheus
from tests.fakes.fake_tools import FakeTools
from tests.fakes.fake_tools.seed import INJECTION_TICKET
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import base_config, csrf, seed_packs, sign_in_as, use_jar


@pytest.fixture(scope="module")
def prom() -> Iterator[FakePrometheus]:
    with FakePrometheus() as p:
        yield p


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as t:
        yield t


class LiteLLMStub:
    """Answers the two monitoring endpoints of LiteLLM for an httpx MockTransport."""

    def __init__(self, aliases: list[str]) -> None:
        self.aliases = aliases

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/liveliness":
            return httpx.Response(200, text='"I\'m alive!"')
        if request.url.path == "/model/info":
            return httpx.Response(
                200,
                json={"data": [{"model_name": a, "litellm_params": {"model": f"openai/{a}"}} for a in self.aliases]},
            )
        return httpx.Response(404)


@pytest.fixture
def app(
    make_client: Callable[..., TestClient],
    oidc: FakeGitLabOIDC,
    prom: FakePrometheus,
    tools: FakeTools,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> TestClient:
    prom.reset()
    tools.reset()
    monkeypatch.setenv("INFERENCE_PROMETHEUS_URL", prom.url)
    for k, v in {
        "JIRA_URL": tools.url_for("jira"),
        "JIRA_READ_EMAIL": "r@example.test",
        "JIRA_READ_TOKEN": tools.token("jira", "read"),
        "JIRA_WRITE_EMAIL": "w@example.test",
        "JIRA_WRITE_TOKEN": tools.token("jira", "write"),
    }.items():
        monkeypatch.setenv(k, v)
    config = base_config(oidc.issuer)
    config["inference"]["prometheus"]["url"] = prom.url
    client = make_client(config=config, llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "a")
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    return {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "erin", "victor")}


def as_user(app: TestClient, jars: dict[str, dict[str, str]], user: str) -> dict[str, str]:
    use_jar(app, jars[user])
    return csrf(app)


def run_db(app: TestClient, fn: Callable[[Any], Any]) -> Any:
    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> Any:
        async with state.sessions() as db, db.begin():
            return await fn(db)

    return app.portal.call(go)  # type: ignore[union-attr]


def mission_with_usage(app: TestClient, h: dict[str, str], tokens: tuple[int, int]) -> str:
    created = app.post("/api/v1/missions", json={"playbook": "summarize-file", "inputs": {"file": "a.md"}}, headers=h)
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    runner.events(
        {
            "ts": datetime.now(UTC).isoformat(),
            "type": "usage",
            "payload": {"model": "z-heavy", "tokens_in": tokens[0], "tokens_out": tokens[1], "cached_tokens": 10},
        }
    )
    runner.complete(output={"title": "t", "bullets": ["a"], "source": "a.md"})
    return str(created.json()["id"])


# ---------------------------------------------------------------- usage and budgets


def test_usage_is_recorded_and_aggregated(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    h = as_user(app, jars, "bob")
    mission_with_usage(app, h, (1000, 200))
    mission_with_usage(app, h, (500, 100))
    by_model = app.get("/api/v1/usage", params={"group_by": "model"}).json()
    [series] = by_model["series"]
    assert (
        series["key"] == "z-heavy"
        and series["tokens_in"] == 1500
        and series["tokens_out"] == 300
        and series["requests"] == 2
        and series["cached_tokens"] == 20
    )
    by_team = app.get("/api/v1/usage", params={"group_by": "team", "bucket": "day"}).json()
    assert by_team["series"][0]["label"] == "Cyber Ops"
    csv = app.get("/api/v1/usage", params={"group_by": "agent", "format": "csv"})
    assert csv.headers["content-type"].startswith("text/csv") and "summarizer" in csv.text
    assert app.get("/api/v1/usage", params={"bucket": "minute"}).status_code == 422


def test_hard_budgets_block_new_missions(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    h = as_user(app, jars, "bob")
    mission_with_usage(app, h, (3000, 0))
    a = as_user(app, jars, "alice")
    put = app.put(
        "/api/v1/budgets",
        json=[
            {"scope": "org", "limit": 1_000_000, "period": "month"},
            {"scope": "playbook:summarize-file", "limit": 2000, "period": "day", "hard": True},
        ],
        headers=a,
    )
    assert put.status_code == 200, put.text
    budgets = {b["scope"]: b for b in put.json()}
    assert budgets["playbook:summarize-file"]["used"] == 3000 and budgets["org"]["used"] == 3000
    h = as_user(app, jars, "bob")
    blocked = app.post("/api/v1/missions", json={"playbook": "summarize-file", "inputs": {"file": "a.md"}}, headers=h)
    assert blocked.status_code == 402 and blocked.json()["type"] == "urn:z:problem:budget-exceeded"
    assert app.put("/api/v1/budgets", json=[], headers=h).status_code == 403


def test_providers_and_external_c2_refusal(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    a = as_user(app, jars, "alice")
    names = {p["name"] for p in app.get("/api/v1/providers").json()}
    assert "inferX" in names
    bad = app.post(
        "/api/v1/providers",
        json={
            "name": "Leaky",
            "kind": "external",
            "aliases": ["ext-leaky"],
            "data_classes": ["C1", "C2"],
            "enabled": True,
        },
        headers=a,
    )
    assert bad.status_code == 422 and "invariant 2" in bad.text
    ok = app.post(
        "/api/v1/providers",
        json={"name": "Labs", "kind": "external", "aliases": ["ext-labs"], "data_classes": ["C0"], "enabled": True},
        headers=a,
    )
    assert ok.status_code == 201, ok.text
    assert "Labs" in {p["name"] for p in app.get("/api/v1/providers").json()}


def test_inference_health_with_prometheus(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    state = app.app.state.z  # type: ignore[attr-defined]
    original = state.http

    async def swap() -> None:
        stub = httpx.AsyncClient(transport=httpx.MockTransport(LiteLLMStub(["z-heavy", "z-fast", "z-judge"])))
        real = original

        class Router(httpx.AsyncClient):
            async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:  # type: ignore[override]
                if request.url.host == "litellm":
                    return await stub.send(request, **kwargs)
                return await real.send(request, **kwargs)

        state.http = Router()

    app.portal.call(swap)  # type: ignore[union-attr]
    try:
        as_user(app, jars, "victor")
        health = app.get("/api/v1/inference/health").json()
    finally:
        state.http = original
    assert health["gateway"]["ok"] is True and health["prometheus_configured"] is True
    models = {m["alias"]: m for m in health["models"]}
    assert (
        models["z-heavy"]["listed"] is True
        and models["z-heavy"]["requests_running"] == 6
        and models["z-heavy"]["ttft_p95_ms"] == 850.0
    )
    assert models["z-embed"]["listed"] is False and models["z-embed"]["status"] == "down"
    assert health["status"] == "degraded"  # one internal alias is not served
    assert {n["name"] for n in health["nodes"]} == {"gpu-node-1", "gpu-node-2", "gpu-node-3"}
    assert health["preemptions_24h"] == 14


def test_analytics_and_global_stream(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    h = as_user(app, jars, "bob")
    mission_id = mission_with_usage(app, h, (100, 10))
    a = as_user(app, jars, "alice")
    analytics = app.get("/api/v1/analytics").json()
    assert analytics["missions_by_status"]["succeeded"] == 1
    assert analytics["hours_saved"] == [
        {"playbook": "summarize-file", "completed": 1, "baseline_minutes": 5, "hours": 0.08}
    ]
    since = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with app.stream("GET", "/api/v1/stream", headers={"Last-Event-ID": f"{since}|x|0"}) as resp:
        assert resp.status_code == 200
        chunk = ""
        for text in resp.iter_text():
            chunk += text
            if chunk.count("\n\nid: ") >= 3:
                break
    assert f'"mission_id":"{mission_id}"' in chunk and "event: status" in chunk
    assert a


# ---------------------------------------------------------------- intake


def sign(secret: str, body: bytes, ts: int | None = None) -> dict[str, str]:
    stamp = str(ts or int(time.time()))
    mac = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Z-Timestamp": stamp, "X-Z-Signature": f"sha256={mac}", "Content-Type": "application/json"}


def test_webhook_trigger_creates_tasks_and_starts_clean_events(
    app: TestClient, jars: dict[str, dict[str, str]]
) -> None:
    h = as_user(app, jars, "erin")
    created = app.post(
        "/api/v1/triggers",
        json={
            "kind": "webhook",
            "name": "Inbox",
            "playbook": "summarize-file",
            "inputs": {"file": "{{ event.file }}"},
            "auto_start": True,
            "filter": {"type": "doc"},
            "config": {"title": "File {{ event.file }}"},
        },
        headers=h,
    )
    assert created.status_code == 201, created.text
    trig = created.json()
    secret, url = trig["secret"], trig["webhook_url"]
    assert secret.startswith("zwh_") and url.endswith(f"/api/v1/webhooks/{trig['id']}")
    assert app.get("/api/v1/triggers").json()[0].get("secret") is None  # shown once
    path = f"/api/v1/webhooks/{trig['id']}"
    body = json.dumps({"type": "doc", "file": "notes.md"}).encode()
    app.cookies.clear()
    assert app.post(path, content=body, headers={"Content-Type": "application/json"}).status_code == 401
    assert app.post(path, content=body, headers={**sign(secret, body), "X-Z-Signature": "sha256=00"}).status_code == 401
    assert app.post(path, content=body, headers=sign(secret, body, int(time.time()) - 3600)).status_code == 401
    ok = app.post(path, content=body, headers=sign(secret, body))
    assert ok.status_code == 202, ok.text
    assert ok.json()["task_id"] and ok.json()["mission_id"]
    ignored = json.dumps({"type": "other"}).encode()
    assert app.post(path, content=ignored, headers=sign(secret, ignored)).json() == {
        "task_id": None,
        "mission_id": None,
    }
    attack = json.dumps({"type": "doc", "file": "x.md", "note": INJECTION_TICKET}).encode()
    held = app.post(path, content=attack, headers=sign(secret, attack)).json()
    assert held["task_id"] and held["mission_id"] is None  # untrusted content waits for a human
    use_jar(app, jars["erin"])
    tasks = app.get("/api/v1/tasks").json()["items"]
    assert {t["status"] for t in tasks} == {"started", "incoming"} and any(t["title"] == "File notes.md" for t in tasks)
    pending = next(t for t in tasks if t["status"] == "incoming")
    dismissed = app.patch(
        f"/api/v1/tasks/{pending['id']}", json={"action": "dismiss", "reason": "spam"}, headers=csrf(app)
    )
    assert dismissed.json()["status"] == "dismissed"


def test_schedule_trigger_fires_once_per_slot(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    from z_server.db.models import Trigger
    from z_server.workers.intake import fire_schedules

    h = as_user(app, jars, "erin")
    bad = app.post("/api/v1/triggers", json={"kind": "schedule", "config": {"cron": "61 * * * *"}}, headers=h)
    assert bad.status_code == 422
    created = app.post(
        "/api/v1/triggers",
        json={
            "kind": "schedule",
            "name": "Hourly",
            "config": {"cron": "0 * * * *", "timezone": "Europe/Paris"},
            "playbook": "summarize-file",
            "inputs": {"file": "report.md"},
        },
        headers=h,
    ).json()

    async def due(db: Any) -> None:
        await db.execute(
            update(Trigger)
            .where(Trigger.id == created["id"])
            .values(next_fire_at=datetime.now(UTC) - timedelta(minutes=1))
        )

    run_db(app, due)
    state = app.app.state.z  # type: ignore[attr-defined]
    fired = app.portal.call(fire_schedules, state)  # type: ignore[union-attr]
    assert len(fired) == 1
    assert app.portal.call(fire_schedules, state) == []  # type: ignore[union-attr]
    task = app.get("/api/v1/tasks").json()["items"][0]
    assert (
        task["source"] == "schedule" and task["status"] == "incoming" and task["suggested_playbook"] == "summarize-file"
    )
    started = app.patch(
        f"/api/v1/tasks/{task['id']}", json={"action": "start", "inputs": {"file": "report.md"}}, headers=csrf(app)
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "started" and started.json()["mission_id"]


def test_jira_trigger_polls_labelled_issues(app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools) -> None:
    from z_server.workers.intake import poll_jira

    a = as_user(app, jars, "alice")
    ops = app.get("/api/v1/connectors/jira/operations").json()
    app.post(
        "/api/v1/connectors/jira/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=a,
    )
    token = base64.b64encode(b"w@example.test:" + tools.token("jira", "write").encode()).decode()
    with tools.client() as c:
        made = c.post(
            "/jira/rest/api/3/issue",
            json={
                "fields": {
                    "project": {"key": "SECOPS"},
                    "summary": "Please triage CVE-2026-0042",
                    "issuetype": {"name": "Task"},
                    "labels": ["z"],
                }
            },
            headers={"Authorization": f"Basic {token}"},
        )
    assert made.status_code == 201, made.text
    h = as_user(app, jars, "erin")
    created = app.post(
        "/api/v1/triggers",
        json={"kind": "jira", "name": "SECOPS z", "config": {"project": "SECOPS", "label": "z"}},
        headers=h,
    )
    assert created.status_code == 201, created.text
    state = app.app.state.z  # type: ignore[attr-defined]
    first = app.portal.call(poll_jira, state)  # type: ignore[union-attr]
    assert first  # the seed already has labelled issues too
    later = datetime.now(UTC) + timedelta(minutes=10)
    assert app.portal.call(poll_jira, state, later) == []  # type: ignore[union-attr]  # deduplicated by key
    tasks = {t["external_ref"]: t for t in app.get("/api/v1/tasks", params={"source": "jira"}).json()["items"]}
    assert len(tasks) == len(first)
    assert "CVE-2026-0042" in tasks[made.json()["key"]]["title"]
