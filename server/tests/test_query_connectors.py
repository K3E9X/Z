"""GraphQL and database connectors through the gateway: declared documents only, variables from the agent,
mutations refused for reads, database queries in a READ ONLY transaction (real Postgres)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_tools import FakeTools
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.missions import RecordingExecutor, ScriptedRunner, dispatch
from tests.support.server import csrf, seed_packs, sign_in_as, use_jar

GRAPHQL = {
    "name": "inventory",
    "kind": "graphql",
    "base_url": "${GQL_URL}",
    "auth": {
        "read": {"type": "bearer", "secret": "env:GQL_RO"},
        "write": {"type": "bearer", "secret": "env:GQL_RW"},
    },
    "declared": [
        {
            "id": "assets.search",
            "query": "query Assets($env: String) { assets(env: $env) { id name env owner } }",
            "input_schema": {"type": "object", "properties": {"env": {"type": "string"}}},
        },
        {
            "id": "assets.tag",
            "query": "mutation Tag($id: ID!, $tag: String!) { tagAsset(id: $id, tag: $tag) { id tags } }",
            "input_schema": {
                "type": "object",
                "required": ["id", "tag"],
                "properties": {"id": {"type": "string"}, "tag": {"type": "string"}},
            },
        },
    ],
    "data_class": "C1",
}


def database_connector() -> dict[str, Any]:
    return {
        "name": "assetdb",
        "kind": "database",
        "base_url": "${PG_URL}",
        "auth": {"read": {"type": "basic", "username": "${PG_USER}", "secret": "env:PG_PASSWORD"}},
        "declared": [
            {
                "id": "assets.search",
                "query": "SELECT name, owner FROM z_demo_assets WHERE env = %(env)s ORDER BY name",
                "input_schema": {"type": "object", "required": ["env"], "properties": {"env": {"type": "string"}}},
            },
            # passes the keyword check; the READ ONLY transaction is what stops it
            {"id": "probe.get", "query": "SELECT nextval('z_demo_seq') AS n", "input_schema": {"type": "object"}},
        ],
        "data_class": "C1",
    }


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as fake:
        yield fake


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], tools: FakeTools, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE TABLE z_demo_assets (name text, owner text, env text)")
        conn.execute("INSERT INTO z_demo_assets VALUES ('edge-01','netops','prod'),('lab-01','rnd','lab')")
        conn.execute("CREATE SEQUENCE z_demo_seq")
    parsed = urlparse(dsn)
    for k, v in {
        "GQL_URL": tools.url_for("graphql"),
        "GQL_RO": "ro-graphql",
        "GQL_RW": "rw-graphql",
        "PG_URL": f"postgresql://{parsed.hostname}:{parsed.port}{parsed.path}",
        "PG_USER": parsed.username or "",
        "PG_PASSWORD": parsed.password or "",
    }.items():
        monkeypatch.setenv(k, v)
    client = make_client(llm_keys=RecordingKeyAdmin())
    seed_packs(client)
    return client


@pytest.fixture
def jars(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, str]]:
    jars = {u: sign_in_as(app, oidc, u) for u in ("alice", "bob", "carol")}
    use_jar(app, jars["alice"])
    for manifest in (GRAPHQL, database_connector()):
        created = app.post("/api/v1/connectors", json=manifest, headers=csrf(app))
        assert created.status_code == 201, created.text
        ops = app.get(f"/api/v1/connectors/{manifest['name']}/operations").json()
        approved = app.post(
            f"/api/v1/connectors/{manifest['name']}/approve-exposure",
            json={"operations": [{"id": o["id"], "exposed": True} for o in ops]},
            headers=csrf(app),
        )
        assert approved.status_code == 200, approved.text
    return jars


def start(app: TestClient, jars: dict[str, dict[str, str]], connectors: list[str], writes: list[str]) -> ScriptedRunner:
    use_jar(app, jars["bob"])
    plan = {"steps": [{"id": "s", "agent": "orchestrator", "prompt": "Look", "writes": writes}], "writes": writes}
    created = app.post(
        "/api/v1/missions",
        json={"title": "Assets", "prompt": "Look", "plan": plan, "connectors": connectors},
        headers=csrf(app),
    )
    assert created.status_code == 201, created.text
    executor = RecordingExecutor()
    [job_id] = dispatch(app, executor)
    runner = ScriptedRunner.for_job(app, executor, job_id)
    runner.get_spec()
    return runner


def call(app: TestClient, runner: ScriptedRunner, name: str, args: dict[str, Any]) -> dict[str, Any]:
    resp = app.post(
        "/gateway/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}},
        headers={"Authorization": f"Bearer {runner.session}", "Accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200, resp.text
    return dict(resp.json()["result"])


def test_graphql_read_and_write(app: TestClient, jars: dict[str, dict[str, str]], tools: FakeTools) -> None:
    runner = start(app, jars, ["inventory"], ["inventory.assets.tag"])
    result = call(app, runner, "inventory__assets__search", {"env": "prod"})
    assert result["isError"] is False, result
    text = result["content"][0]["text"]
    assert "edge-01" in text and "lab-01" not in text
    proposed = runner.call(
        "POST",
        "/actions",
        json={
            "connector": "inventory",
            "operation": "inventory.assets.tag",
            "summary": "tag edge-01",
            "payload": {"id": "a1", "tag": "patched"},
        },
    )
    assert proposed.status_code == 201, proposed.text
    use_jar(app, jars["carol"])
    action_id = proposed.json()["id"]
    assert app.post(f"/api/v1/actions/{action_id}/approve", json={}, headers=csrf(app)).status_code == 200
    from z_server.gateway import executor

    state = app.app.state.z  # type: ignore[attr-defined]
    assert app.portal.call(executor.tick, state, state.gateway)["applied"] == [action_id]  # type: ignore[union-attr]
    [write] = tools.writes("graphql.tag")
    assert write["token_kind"] == "write" and write["data"] == {"id": "a1", "tag": "patched"}


def test_graphql_read_with_a_mutation_is_refused(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    use_jar(app, jars["alice"])
    bad = {**GRAPHQL, "name": "inventory2"}
    bad["declared"] = [{"id": "assets.search", "query": "mutation { dropAll }"}]
    refused = app.post("/api/v1/connectors", json=bad, headers=csrf(app))
    assert refused.status_code == 422 and "cannot contain a mutation" in refused.text


def test_database_reads_in_a_read_only_transaction(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    runner = start(app, jars, ["assetdb"], [])
    result = call(app, runner, "assetdb__assets__search", {"env": "prod"})
    assert result["isError"] is False, result
    text = result["content"][0]["text"]
    assert '"name":"edge-01"' in text and "lab-01" not in text and '"row_count":1' in text
    injected = call(app, runner, "assetdb__assets__search", {"env": "prod' OR '1'='1"})
    assert '"row_count":0' in injected["content"][0]["text"]  # a bound parameter, never SQL
    probe = call(app, runner, "assetdb__probe__get", {})
    assert probe["isError"] is True and "read-only transaction" in probe["content"][0]["text"]


def test_database_connectors_refuse_writes_and_free_sql(app: TestClient, jars: dict[str, dict[str, str]]) -> None:
    use_jar(app, jars["alice"])
    bad = database_connector() | {"name": "assetdb2"}
    bad["declared"] = [{"id": "assets.purge", "query": "DELETE FROM z_demo_assets"}]
    refused = app.post("/api/v1/connectors", json=bad, headers=csrf(app))
    assert refused.status_code == 422 and "starts with SELECT or WITH" in refused.text
