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
