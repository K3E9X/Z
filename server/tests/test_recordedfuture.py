"""Recorded Future through the vuln pack connector (Connect API subset): read-only enrichment with the gateway's
token, only the requested fields, 404 as "nothing known", and internal indicators never sent out."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], tools: FakeTools, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> TestClient:
    tools.reset()
    monkeypatch.setenv("RECORDEDFUTURE_API_URL", tools.url_for("recordedfuture"))
    monkeypatch.setenv("RECORDEDFUTURE_TOKEN", tools.token("recordedfuture", "read"))
    client = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "a")
    seed_packs(client)
    return client


@pytest.fixture
def runner(app: TestClient, oidc: FakeGitLabOIDC) -> ScriptedRunner:
    use_jar(app, sign_in_as(app, oidc, "alice"))
    ops = app.get("/api/v1/connectors/recordedfuture/operations").json()
    assert {o["id"]: o["class"] for o in ops} == {
        f"recordedfuture.{op}": "read"
        for op in ("ip.get", "domain.get", "hash.get", "url.get", "vulnerability.get", "malware.get", "riskrules.list")
    }
    app.post(
        "/api/v1/connectors/recordedfuture/approve-exposure",
        json={"operations": [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]},
        headers=csrf(app),
    ).raise_for_status()
    plan = {"steps": [{"id": "s", "agent": "vuln-analyst", "prompt": "Triage"}], "writes": []}
    created = app.post(
        "/api/v1/missions",
        json={"title": "Triage", "prompt": "Triage", "plan": plan, "connectors": ["recordedfuture"]},
        headers=csrf(app),
    )
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    scripted = ScriptedRunner.for_job(app, executor, job_id)
    scripted.get_spec()
    return scripted


def call(app: TestClient, runner: ScriptedRunner, name: str, args: dict[str, Any]) -> dict[str, Any]:
    resp = app.post(
        "/gateway/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
        headers={"Authorization": f"Bearer {runner.session}", "Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["result"])


def test_a_cve_is_enriched_with_the_requested_fields(app: TestClient, runner: ScriptedRunner, tools: FakeTools) -> None:
    names = [t["name"] for t in runner.spec["tools"]["gateway"]]
    assert "recordedfuture__vulnerability__get" in names  # the vuln analyst gets the lookups
    cve = call(
        app,
        runner,
        "recordedfuture__vulnerability__get",
        {"cve": "CVE-2021-44228", "fields": "entity,risk,lifecycleStage"},
    )
    text = cve["content"][0]["text"]
    assert cve["isError"] is False and "Exploited in the Wild" in text and "Exploited" in text
    assert "cvssv3" not in text  # not asked
    assert tools.token("recordedfuture", "read") not in text
    unknown = call(app, runner, "recordedfuture__vulnerability__get", {"cve": "CVE-2026-99999"})
    assert unknown["isError"] is True and "404" in unknown["content"][0]["text"]
    rules = call(app, runner, "recordedfuture__riskrules__list", {"entity_type": "vulnerability"})
    assert rules["isError"] is False and "linkedToRansomware" in rules["content"][0]["text"]


def test_internal_indicators_never_leave(app: TestClient, runner: ScriptedRunner, tools: FakeTools) -> None:
    public = call(app, runner, "recordedfuture__ip__get", {"ip": "185.220.101.1"})
    assert public["isError"] is False and "Malicious" in public["content"][0]["text"]
    for name, args in (
        ("recordedfuture__ip__get", {"ip": "10.20.30.40"}),
        ("recordedfuture__ip__get", {"ip": "192.168.1.10"}),
        ("recordedfuture__ip__get", {"ip": "fd12:3456::1"}),
        ("recordedfuture__domain__get", {"domain": "pay-db.corp"}),
        ("recordedfuture__url__get", {"url": "http://10.0.0.1/admin"}),
    ):
        refused = call(app, runner, name, args)
        assert refused["isError"] is True, (name, args)
    sent = [w["data"]["entity"] for w in tools.writes() if w["tool"] == "recordedfuture"]
    assert sent == ["185.220.101.1"]  # only the public address reached Recorded Future
