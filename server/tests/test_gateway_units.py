"""Unit tests of the gateway building blocks."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from z_server.executors.docker import demux
from z_server.gateway import adf
from z_server.gateway import http as h
from z_server.gateway.auth import AuthError, material
from z_server.gateway.core import shape, with_defaults
from z_server.gateway.limits import CallStats, SlidingWindow
from z_server.gateway.secrets import SecretError, SecretResolver


def test_adf_round_trip_keeps_structure_and_drops_unsafe_links() -> None:
    text = "# Title\n\nSee **bold** and `code` and [doc](https://example.test/x).\n\n- one\n- two\n\n```sh\nrm -rf /tmp/x\n```"
    doc = adf.to_adf(text)
    assert doc["type"] == "doc" and doc["version"] == 1
    assert [b["type"] for b in doc["content"]] == ["heading", "paragraph", "bulletList", "codeBlock"]
    back = adf.to_text(doc)
    assert "# Title" in back and "[doc](https://example.test/x)" in back and "- one" in back and "rm -rf /tmp/x" in back
    unsafe = adf.to_text(
        {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "x",
                            "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}],
                        }
                    ],
                }
            ],
        }
    )
    assert unsafe == "x"
    assert adf.to_adf("")["content"] == [{"type": "paragraph", "content": []}]


def test_shape_flattens_nested_adf_and_noise() -> None:
    value = {"issues": [{"fields": {"description": adf.to_adf("hello"), "avatarUrls": {"x": "y"}}}], "expand": "x"}
    assert shape(value) == {"issues": [{"fields": {"description": "hello"}}]}


def test_prepare_maps_path_query_and_body() -> None:
    schema = {"properties": {"q": {"x-in": "query"}, "trace": {"x-in": "header"}}}
    get = h.prepare(
        "https://t", "GET /issue/{key}/comment", schema, {"key": "SEC 1", "q": "a", "limit": 5, "trace": "t1"}
    )
    assert (
        get.url == "https://t/issue/SEC%201/comment"
        and get.params == {"q": "a", "limit": 5}
        and get.headers == {"trace": "t1"}
    )
    post = h.prepare("https://t", "POST /issue", {}, {"fields": {"a": 1}, "notify": False})
    assert post.json == {"fields": {"a": 1}, "notify": False} and post.params == {}
    body = h.prepare("https://t", "PUT /x/{id}", {"properties": {"body": {}}}, {"id": 1, "body": {"k": "v"}})
    assert body.json == {"k": "v"}
    with pytest.raises(h.InvokeError):
        h.prepare("https://t", "GET /x/{id}", {}, {})
    with pytest.raises(h.InvokeError):
        h.prepare("https://t", "mcp:tool", {}, {})
    assert with_defaults({"properties": {"n": {"default": 20}, "m": {}}}, {"m": 1}) == {"m": 1, "n": 20}


def test_send_retries_idempotent_reads_on_429_only() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    async def go() -> tuple[h.HttpResult, h.HttpResult]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            read = await h.send(client, h.prepare("https://t", "GET /a", {}, {}), {})
            calls.clear()
            write = await h.send(client, h.prepare("https://t", "POST /a", {}, {"x": 1}), {})
            return read, write

    read, write = asyncio.run(go())
    assert read.status == 200 and read.attempts == 2 and read.body == {"ok": True}
    assert write.status == 429 and write.attempts == 1  # a write is never replayed by the gateway


def test_secret_backends(tmp_path: Path) -> None:
    secret_file = tmp_path / "token"
    secret_file.write_text("from-file\n")

    def vault(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Vault-Token"] == "vt"
        assert request.url.path == "/v1/secret/data/z/jira"
        return httpx.Response(200, json={"data": {"data": {"token": "from-vault"}}})

    async def go() -> list[str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(vault)) as client:
            r = SecretResolver(client, env={"TOK": "from-env", "VAULT_ADDR": "https://vault", "VAULT_TOKEN": "vt"})
            values = [
                await r.resolve("env:TOK"),
                await r.resolve(f"file:{secret_file}"),
                await r.resolve("vault:secret/z/jira#token"),
            ]
            for bad in ("env:MISSING", "file:relative", "vault:secret/z/jira#nope", "s3:x", "kubernetes:ns/name#k"):
                with pytest.raises(SecretError) as exc:
                    await r.resolve(bad)
                assert "from-" not in str(exc.value)  # errors never carry values
            return values

    assert asyncio.run(go()) == ["from-env", "from-file", "from-vault"]


def test_auth_material_and_separate_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("READ_T", "r-token")
    monkeypatch.setenv("WRITE_T", "w-token")
    monkeypatch.setenv("JIRA_READ_EMAIL", "bot@example.test")

    async def go(manifest: dict[str, Any], purpose: str) -> Any:
        async with httpx.AsyncClient() as client:
            return await material(manifest, purpose, SecretResolver(client))  # type: ignore[arg-type]

    basic = {
        "name": "jira",
        "auth": {
            "read": {"type": "basic", "username": "${JIRA_READ_EMAIL}", "secret": "env:READ_T"},
            "write": {"type": "basic", "username": "w@example.test", "secret": "env:WRITE_T"},
        },
    }
    m = asyncio.run(go(basic, "read"))
    assert (
        m.headers["Authorization"] == "Basic " + base64.b64encode(b"bot@example.test:r-token").decode()
        and m.identity == "bot@example.test"
    )
    assert "r-token" in m.secrets
    same = {
        "name": "x",
        "auth": {
            "read": {"type": "bearer", "secret": "env:READ_T"},
            "write": {"type": "bearer", "secret": "env:READ_T"},
        },
    }
    with pytest.raises(AuthError, match="invariant 7"):
        asyncio.run(go(same, "write"))
    with pytest.raises(AuthError, match="no write credential"):
        asyncio.run(go({"name": "x", "auth": {"read": {"type": "none"}}}, "write"))
    key = asyncio.run(
        go(
            {
                "name": "x",
                "auth": {
                    "read": {"type": "api_key", "header": "Authorization", "prefix": "Token", "secret": "env:READ_T"}
                },
            },
            "read",
        )
    )
    assert key.headers == {"Authorization": "Token r-token"}


def test_sliding_window_and_stats() -> None:
    w = SlidingWindow()
    assert w.allow("k", 2, now=0) == 0 and w.allow("k", 2, now=1) == 0
    assert w.allow("k", 2, now=2) == pytest.approx(58)
    assert w.allow("k", 2, now=61) == 0
    stats = CallStats()
    for ms, ok in [(10, True), (20, True), (300, False)]:
        stats.record("jira", ms, ok)
    s = stats.summary("jira")
    assert (
        s["calls"] == 3
        and s["latency_p50_ms"] == 20
        and s["latency_p95_ms"] == 300
        and s["error_rate"] == pytest.approx(1 / 3, abs=1e-3)
    )


def test_docker_log_demux() -> None:
    frame = (
        bytes([1, 0, 0, 0])
        + len(b"out\n").to_bytes(4, "big")
        + b"out\n"
        + bytes([2, 0, 0, 0])
        + (4).to_bytes(4, "big")
        + b"err\n"
    )
    assert demux(frame) == "out\nerr\n"
    assert demux(b"plain text") == "plain text"
    assert json.loads(json.dumps(demux(b""))) == ""


def test_raw_text_bodies_from_the_spec_are_sent_as_is() -> None:
    from z_common.connectors.operations import from_openapi

    spec = {
        "openapi": "3.0.3",
        "paths": {
            "/items/{parent_id}:/{filename}:/content": {
                "put": {
                    "operationId": "files.upload",
                    "parameters": [
                        {"name": "parent_id", "in": "path", "required": True},
                        {"name": "filename", "in": "path", "required": True},
                    ],
                    "requestBody": {"required": True, "content": {"text/plain": {"schema": {"type": "string"}}}},
                }
            }
        },
    }
    (op,) = from_openapi({"name": "m365", "kind": "openapi"}, spec)
    assert op.input_schema["properties"]["body"]["x-content-type"] == "text/plain"
    req = h.prepare(
        "https://g", op.source_ref, op.input_schema, {"parent_id": "p1", "filename": "a b.md", "body": "# x"}
    )
    assert req.url == "https://g/items/p1:/a%20b.md:/content" and req.content == b"# x" and req.json is None
    assert req.headers["Content-Type"] == "text/plain"
    with pytest.raises(h.InvokeError, match="as text"):
        h.prepare("https://g", op.source_ref, op.input_schema, {"parent_id": "p", "filename": "f", "body": {"a": 1}})
    with pytest.raises(h.InvokeError, match="larger than"):
        big = "x" * (h.MAX_RAW_BODY_BYTES + 1)
        h.prepare("https://g", op.source_ref, op.input_schema, {"parent_id": "p", "filename": "f", "body": big})


def test_send_puts_the_raw_body_on_the_wire() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": "item1"})

    req = h.PreparedRequest("PUT", "https://g/items/p:/f.md:/content", {}, None, {"Content-Type": "text/plain"}, b"hi")

    async def go() -> h.HttpResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await h.send(client, req, {"Authorization": "Bearer t"})

    result = asyncio.run(go())
    assert result.status == 201 and seen[0].content == b"hi" and seen[0].headers["content-type"] == "text/plain"


def test_error_body_turns_a_2xx_error_answer_into_a_failure() -> None:
    from z_server.gateway.http import outcome

    slack = {"error_body": {"path": "ok", "equals": False, "message": "error"}}
    assert outcome(slack, 200, {"ok": True, "messages": []}) == (True, None)
    assert outcome(slack, 200, {"ok": False, "error": "channel_not_found"}) == (False, "channel_not_found")
    assert outcome(slack, 200, ["not", "an", "object"]) == (True, None)
    assert outcome(slack, 503, {"ok": True}) == (False, "HTTP 503")
    nested = {"error_body": {"path": "result.status", "equals": "error"}}
    assert outcome(nested, 200, {"result": {"status": "error"}})[0] is False
    assert outcome({}, 200, {"ok": False}) == (True, None)  # no rule: the status decides


@pytest.mark.parametrize("value", ["..", ".", ""])
def test_path_parameters_cannot_be_dot_segments(value: str) -> None:
    from z_server.gateway.http import InvokeError, prepare

    with pytest.raises(InvokeError, match="cannot be"):
        prepare("https://jira.example.internal", "GET /rest/api/3/issue/{key}", {}, {"key": value})
    ok = prepare("https://jira.example.internal", "GET /rest/api/3/issue/{key}", {}, {"key": "a/../b"})
    assert ok.url.endswith("/issue/a%2F..%2Fb")


def test_write_errors_are_redacted_before_they_reach_the_action_and_the_audit() -> None:
    from z_server.gateway.core import Invocation
    from z_server.gateway.writers import WriteError, _fail

    inv = Invocation(False, 200, {"ok": False, "echo": "token rw-slack-123"}, 5, "z-write", ["rw-slack-123"], "bad")
    with pytest.raises(WriteError) as caught:
        _fail(inv, "post")
    assert "rw-slack-123" not in str(caught.value) and "post: bad" in str(caught.value)
