"""Browser connectors end to end: gateway -> real z-browser worker (Chromium) -> the fake FW Console web UI."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.fakes._server import FakeServer
from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import REPO, csrf, seed_packs, sign_in_as, use_jar

pytest.importorskip("playwright")
BROWSERS = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache/ms-playwright")
if not any(BROWSERS.glob("chromium*")):
    pytest.skip("no Chromium build installed for Playwright", allow_module_level=True)

TOKEN = "browser-token-0123456789abcdef"


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture(scope="module")
def worker() -> Iterator[FakeServer]:
    from z_browser.app import create_app

    with FakeServer(create_app(TOKEN)) as srv:
        yield srv


@pytest.fixture
def env(tools: FakeTools, monkeypatch: pytest.MonkeyPatch) -> FakeTools:
    tools.reset()
    for k, v in {
        "FWCONSOLE_URL": tools.url_for("fwconsole"),
        "FWCONSOLE_READ_USER": "z-read",
        "FWCONSOLE_READ_PASSWORD": "ro-fwconsole",
        "FWCONSOLE_WRITE_USER": "z-write",
        "FWCONSOLE_WRITE_PASSWORD": "rw-fwconsole",
    }.items():
        monkeypatch.setenv(k, v)
    return tools


@pytest.fixture
def app(make_client: Callable[..., TestClient], tmp_path: Any, env: FakeTools, worker: FakeServer) -> TestClient:
    client = make_client(
        llm_keys=RecordingKeyAdmin(),
        artifacts_dir=tmp_path / "artifacts",
        browser_url=worker.url,
        browser_token=TOKEN,
    )
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}
    use_jar(app, jars["alice"])
    ops = app.get("/api/v1/connectors/fwconsole/operations").json()
    assert {o["id"]: o["class"] for o in ops} == {"fwconsole.rules.search": "read", "fwconsole.rules.comment": "write"}
    assert all(o["source_ref"].startswith("flow:") for o in ops)
    resp = app.post(
        "/api/v1/connectors/fwconsole/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": True} for o in ops]},
        headers=csrf(app),
    )
    assert resp.status_code == 200, resp.text
    return jars


def mcp(app: TestClient, token: str, method: str, params: dict[str, Any] | None = None) -> Any:
    resp = app.post(
        "/gateway/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def start(app: TestClient, jars: dict[str, dict[str, str]]) -> ScriptedRunner:
    use_jar(app, jars["bob"])
    writes = ["fwconsole.rules.comment"]
    plan = {"steps": [{"id": "look", "agent": "browser-agent", "prompt": "Check", "writes": writes}], "writes": writes}
    body = {"title": "Rules", "prompt": "Check the rules", "plan": plan, "connectors": ["fwconsole"]}
    created = app.post("/api/v1/missions", json=body, headers=csrf(app))
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    return runner


def call(app: TestClient, runner: ScriptedRunner, name: str, args: dict[str, Any]) -> dict[str, Any]:
    out = mcp(app, runner.session or "", "tools/call", {"name": name, "arguments": args})
    assert "result" in out, out
    return dict(out["result"])


def executor_tick(app: TestClient) -> dict[str, list[str]]:
    from z_server.gateway import executor

    state = app.app.state.z  # type: ignore[attr-defined]
    return app.portal.call(executor.tick, state, state.gateway)  # type: ignore[union-attr,no-any-return]


def test_read_flow_signs_in_with_the_read_account_and_extracts(
    app: TestClient, jars: dict[str, dict[str, str]]
) -> None:
    runner = start(app, jars)
    listed = {t["name"] for t in mcp(app, runner.session or "", "tools/list")["result"]["tools"]}
    assert listed == {"fwconsole__rules__search"}  # the browser tool lists the mission's browser flows, reads only
    result = call(app, runner, "fwconsole__rules__search", {"q": "deny"})
    assert result["isError"] is False, result["content"][0]["text"]
    text = result["content"][0]["text"]
    assert '"Name":"deny-telnet"' in text and "allow-dns" not in text
    assert "ro-fwconsole" not in text and "rw-fwconsole" not in text
    assert "z-read" in audited_identities(app, "gateway.call")
    browser_events = [e for e in mission_events(app, runner) if e["type"] == "browser"]
    assert browser_events and browser_events[-1]["payload"]["action"] == "fwconsole.rules.search"
    assert browser_events[-1]["payload"]["url"].endswith("/fwconsole/rules?q=deny")
    assert result["_meta"]["io.z/judge"] == "clean"


def mission_events(app: TestClient, runner: ScriptedRunner) -> list[dict[str, Any]]:
    from sqlalchemy import select
    from z_server.db.models import Event, Job

    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> list[dict[str, Any]]:
        async with state.sessions() as db:
            job = await db.get(Job, runner.job_id)
            assert job is not None
            rows = (await db.execute(select(Event).where(Event.mission_id == job.mission_id))).scalars()
            return [{"type": r.type, "payload": r.payload} for r in rows]

    return app.portal.call(go)  # type: ignore[union-attr,no-any-return]


def audited_identities(app: TestClient, action: str) -> set[str]:
    from sqlalchemy import select
    from z_server.db.models import AuditEvent

    state = app.app.state.z  # type: ignore[attr-defined]

    async def go() -> set[str]:
        async with state.sessions() as db:
            rows = await db.execute(select(AuditEvent.runs_as).where(AuditEvent.action == action))
            return {r for (r,) in rows if r}

    return app.portal.call(go)  # type: ignore[union-attr,no-any-return]


def test_page_content_is_judged_like_any_tool_result(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    runner = start(app, jars)
    result = call(app, runner, "fwconsole__rules__search", {"q": "legacy"})
    assert result["_meta"]["io.z/judge"] == "flagged"  # the injection in the rule note is flagged as data


def test_write_flow_runs_in_the_executor_with_the_write_account(
    app: TestClient, jars: dict[str, dict[str, str]], env: FakeTools
) -> None:
    runner = start(app, jars)
    proposed = runner.call(
        "POST",
        "/actions",
        json={
            "connector": "fwconsole",
            "operation": "fwconsole.rules.comment",
            "summary": "Note the legacy block",
            "payload": {"rule": "deny-telnet", "text": "Reviewed with the network team"},
        },
    )
    assert proposed.status_code == 201, proposed.text
    action_id = str(proposed.json()["id"])
    use_jar(app, jars["carol"])
    approved = app.post(f"/api/v1/actions/{action_id}/approve", json={}, headers=csrf(app))
    assert approved.status_code == 200 and approved.json()["status"] == "approved", approved.text
    assert executor_tick(app)["applied"] == [action_id]
    [write] = env.writes("fwconsole.rules.comment")
    assert write["token_kind"] == "write"
    assert write["data"] == {"rule": "deny-telnet", "text": "Reviewed with the network team"}
    action = app.get(f"/api/v1/actions/{action_id}").json()
    assert action["status"] == "applied"


def test_changing_a_flow_needs_a_new_review(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    use_jar(app, jars["alice"])
    manifest = yaml.safe_load((REPO / "packs/archi-isp/connectors/fwconsole.yaml").read_text())
    manifest["flows"]["search_rules"]["steps"].append({"action": "click", "selector": "#rules tr"})
    patched = app.patch("/api/v1/connectors/fwconsole", json={"manifest": manifest}, headers=csrf(app))
    assert patched.status_code == 200, patched.text
    ops = {o["id"]: o for o in app.get("/api/v1/connectors/fwconsole/operations").json()}
    assert ops["fwconsole.rules.search"]["exposed"] is False
    assert ops["fwconsole.rules.comment"]["exposed"] is True  # its flow did not change


def test_flow_rules_are_enforced_on_connector_changes(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    use_jar(app, jars["alice"])
    manifest = yaml.safe_load((REPO / "packs/archi-isp/connectors/fwconsole.yaml").read_text())
    manifest["flows"]["search_rules"]["steps"].append({"action": "click", "selector": "#row-{{args.q}}"})
    refused = app.patch("/api/v1/connectors/fwconsole", json={"manifest": manifest}, headers=csrf(app))
    assert refused.status_code == 422 and "args.q can only be used" in refused.text
    manifest = yaml.safe_load((REPO / "packs/archi-isp/connectors/fwconsole.yaml").read_text())
    manifest["flows"]["search_rules"]["steps"].append({"action": "goto", "url": "/x?p={{auth.password}}"})
    refused = app.patch("/api/v1/connectors/fwconsole", json={"manifest": manifest}, headers=csrf(app))
    assert refused.status_code == 422 and "auth.password can only be typed" in refused.text


def test_without_a_worker_the_call_says_so(
    make_client: Callable[..., TestClient], tmp_path: Any, env: FakeTools, oidc: FakeGitLabOIDC
) -> None:
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "artifacts")
    seed_packs(client)
    jars = {u: sign_in_as(client, oidc, u) for u in ("alice", "bob")}
    use_jar(client, jars["alice"])
    ops = client.get("/api/v1/connectors/fwconsole/operations").json()
    client.post(
        "/api/v1/connectors/fwconsole/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": True} for o in ops]},
        headers=csrf(client),
    )
    runner = start(client, jars)
    result = call(client, runner, "fwconsole__rules__search", {"q": "deny"})
    assert result["isError"] is True and "z-browser worker" in result["content"][0]["text"]


def test_builder_drafts_a_browser_connector(app: TestClient, jars: dict[str, dict[str, str]], env: FakeTools) -> None:
    use_jar(app, jars["alice"])
    source = yaml.safe_load((REPO / "packs/archi-isp/connectors/fwconsole.yaml").read_text())
    body = {
        "source": "browser",
        "name": "fwlab",
        "base_url": env.url_for("fwconsole"),
        "flows": source["flows"],
        "declared": source["declared"],
        "data_class": "C2",
    }
    draft = app.post("/api/v1/connectors/import", json=body, headers=csrf(app))
    assert draft.status_code == 200, draft.text
    out = draft.json()
    assert {o["id"]: o["class"] for o in out["operations"]} == {
        "fwlab.rules.search": "read",
        "fwlab.rules.comment": "write",
    }
    assert out["manifest"]["auth"]["read"] == {
        "type": "basic",
        "username": "${FWLAB_READ_USER}",
        "secret": "env:FWLAB_READ_PASSWORD",
    }
    created = app.post("/api/v1/connectors", json=out["manifest"], headers=csrf(app))
    assert created.status_code == 201, created.text
    assert created.json()["review_status"] == "pending_review"
    body["declared"] = [{**source["declared"][0], "flow": "missing"}]
    refused = app.post("/api/v1/connectors/import", json=body, headers=csrf(app))
    assert refused.status_code == 422 and "not defined in flows" in refused.text


async def _boxes(url: str, selectors: list[str]) -> dict[str, tuple[float, float]]:
    """Centers of elements on the page as the recorder renders it (same viewport)."""
    from playwright.async_api import async_playwright

    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await b.new_page(viewport={"width": 1280, "height": 800})
        await page.goto(url)
        for sel in selectors:
            box = await page.locator(sel).first.bounding_box()
            assert box is not None
            out[sel] = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await b.close()
    return out


def test_record_a_flow_from_the_web_app(app: TestClient, jars: dict[str, dict[str, str]], env: FakeTools) -> None:
    import asyncio

    use_jar(app, jars["alice"])
    started = app.post(
        "/api/v1/connectors/record-browser",
        json={"connector": "fwconsole", "start_url": "login", "name": "find rules"},
        headers=csrf(app),
    )
    assert started.status_code == 201, started.text
    rec = started.json()
    assert rec["viewer_url"].endswith(f"/connectors/record/{rec['id']}")
    base = f"/api/v1/connectors/recordings/{rec['id']}"
    state = app.get(base).json()
    assert state["url"] == "login" and state["purpose"] == "read" and state["steps"] == []
    screen = app.get(f"{base}/screen")
    assert screen.status_code == 200 and screen.headers["content-type"] == "image/jpeg"
    assert screen.content[:2] == b"\xff\xd8"

    login = asyncio.run(_boxes(env.url_for("fwconsole") + "/login", ["#username", "#password", "#signin"]))

    def send(body: dict[str, Any]) -> dict[str, Any]:
        resp = app.post(f"{base}/input", json=body, headers=csrf(app))
        assert resp.status_code == 200, resp.text
        return dict(resp.json())

    for field, sel in (("username", "#username"), ("password", "#password")):
        x, y = login[sel]
        send({"kind": "click", "x": x, "y": y})
        send({"kind": "credential", "field": field})
    x, y = login["#signin"]
    send({"kind": "click", "x": x, "y": y})
    send({"kind": "goto", "url": "rules?q=deny"})
    state = app.get(base).json()
    assert state["url"].startswith("rules")
    refused = app.post(f"{base}/input", json={"kind": "goto", "url": "https://example.org/"}, headers=csrf(app))
    assert refused.status_code == 422 and "not an allowed host" in refused.text
    send({"kind": "screenshot", "name": "rules"})

    use_jar(app, jars["bob"])  # another person cannot drive it
    assert app.get(base).status_code in {403, 404}
    use_jar(app, jars["alice"])
    draft = app.post(f"{base}/finish", json={}, headers=csrf(app))
    assert draft.status_code == 200, draft.text
    flow = draft.json()["flow"]
    assert flow["start_url"] == "login"
    actions = [s["action"] for s in flow["steps"]]
    assert actions == ["click", "fill", "click", "fill", "click", "goto", "screenshot"]
    assert flow["steps"][3]["value"] == "{{auth.password}}" and "ro-fwconsole" not in str(flow)
    assert app.get(base).status_code == 404  # finished

    manifest = yaml.safe_load((REPO / "packs/archi-isp/connectors/fwconsole.yaml").read_text())
    manifest["flows"]["recorded"] = flow
    manifest["declared"].append({"id": "rules.overview", "flow": "recorded", "input_schema": {"type": "object"}})
    saved = app.patch("/api/v1/connectors/fwconsole", json={"manifest": manifest}, headers=csrf(app))
    assert saved.status_code == 200, saved.text
    ops = {o["id"]: o for o in app.get("/api/v1/connectors/fwconsole/operations").json()}
    assert ops["fwconsole.rules.overview"]["exposed"] is False  # a recorded flow is reviewed before exposure
