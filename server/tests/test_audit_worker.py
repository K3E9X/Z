"""Audit worker (docs/12 §4): scheduled signed checkpoints and at-least-once forwarding (syslog over TLS, HTTPS)."""

from __future__ import annotations

import datetime as dt
import json
import socket
import ssl
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from z_server.db.models import AuditEvent
from z_server.workers import audit as worker

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import csrf, sign_in_as


@pytest.fixture
def admin(client: TestClient, oidc: FakeGitLabOIDC) -> TestClient:
    sign_in_as(client, oidc, "alice")
    return client


def _run(client: TestClient, fn: Callable[[Any], Any]) -> Any:
    state = client.app.state.z  # type: ignore[attr-defined]

    async def go() -> Any:
        return await fn(state)

    return client.portal.call(go)  # type: ignore[union-attr]


def _tick(client: TestClient, senders: dict[str, worker.Sender] | None = None) -> dict[str, Any]:
    return dict(_run(client, lambda state: worker.tick(state, senders)))


def _event(client: TestClient) -> None:
    client.post("/api/v1/teams", json={"name": f"t{dt.datetime.now().timestamp()}"}, headers=csrf(client))


def _forwarding(client: TestClient, **targets: dict[str, Any]) -> dict[str, Any]:
    body = client.get("/api/v1/admin/audit-settings").json()
    body["forward"] = targets
    resp = client.put("/api/v1/admin/audit-settings", json=body, headers=csrf(client))
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def test_checkpoints_are_signed_on_schedule_and_only_when_the_chain_moved(admin: TestClient, database_url: str) -> None:
    first = _tick(admin)["checkpoint"]
    assert first is not None
    assert _tick(admin)["checkpoint"] is None  # only the checkpoint's own event since
    _event(admin)
    assert _tick(admin)["checkpoint"] is None  # not due yet (60 minutes)
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as conn:  # owner role: age the last checkpoint
        conn.execute("UPDATE audit_checkpoints SET ts = ts - interval '61 minutes'")
    second = _tick(admin)["checkpoint"]
    assert second is not None and second > first
    verified = admin.post("/api/v1/admin/audit:verify", json={}, headers=csrf(admin)).json()
    assert verified["ok"] is True and verified["checkpoints_verified"] == 2
    events = admin.get("/api/v1/audit", params={"action": "audit.checkpoint"}).json()["items"]
    assert len(events) == 2 and all(e["actor"] == "system" for e in events)


def test_forwarding_is_at_least_once_with_a_cursor_per_target(admin: TestClient) -> None:
    received: list[int] = []
    failing = {"on": False}

    async def sender(url: str, target: dict[str, Any], rows: list[AuditEvent]) -> None:
        if failing["on"]:
            raise worker.ForwardError("the log endpoint answered HTTP 503")
        received.extend(r.seq for r in rows)

    senders: dict[str, worker.Sender] = {"https": sender, "syslog": sender}
    _forwarding(admin, https={"enabled": True, "url": "https://localhost:9/z"})
    _tick(admin, senders)  # a new destination starts from now: the settings change itself is already behind it
    assert received == [] or all(seq > 0 for seq in received)
    received.clear()
    _event(admin)
    _event(admin)
    _tick(admin, senders)
    assert len(received) >= 2 and received == sorted(received)
    failing["on"] = True
    _event(admin)
    _tick(admin, senders)
    status = admin.get("/api/v1/admin/audit-settings").json()["forward"]["https"]["status"]
    assert status["last_error"] == "the log endpoint answered HTTP 503" and status["last_seq"] == received[-1]
    failing["on"] = False
    before = list(received)
    _tick(admin, senders)
    assert received[: len(before)] == before and received[len(before)] == before[-1] + 1  # nothing lost or skipped
    status = admin.get("/api/v1/admin/audit-settings").json()["forward"]["https"]["status"]
    assert status["last_error"] is None and status["last_seq"] == received[-1]


def test_https_sender_posts_json_lines_with_the_referenced_token(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("Z_AUDIT_TOKEN", "audit-token-123")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    from z_server.gateway.secrets import SecretResolver

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    send = worker.https_sender(client, SecretResolver(client))

    async def go(state: Any) -> None:
        async with state.sessions() as db:
            rows = list((await db.execute(__import__("sqlalchemy").select(AuditEvent).limit(3))).scalars())
        await send("https://logs.example.internal/z", {"token_ref": "env:Z_AUDIT_TOKEN"}, rows)

    _run(admin, go)
    assert seen[0].headers["authorization"] == "Bearer audit-token-123"
    assert seen[0].headers["content-type"] == "application/x-ndjson"
    lines = [json.loads(line) for line in seen[0].content.decode().splitlines()]
    assert lines and {"seq", "hash", "prev_hash", "action"} <= set(lines[0])


def _self_signed(tmp: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5))
        .not_valid_after(dt.datetime.now(dt.UTC) + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp / "syslog.crt", tmp / "syslog.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return cert_path, key_path


def test_syslog_forwarding_over_tls_uses_octet_counting(
    admin: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cert, key = _self_signed(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    chunks: list[bytes] = []

    def serve() -> None:
        conn, _ = listener.accept()
        with server_ctx.wrap_socket(conn, server_side=True) as tls:
            while data := tls.recv(65536):
                chunks.append(data)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    _forwarding(admin, syslog={"enabled": True, "url": f"tls://localhost:{port}", "from_now": False})
    out = _tick(admin)
    thread.join(5)
    listener.close()
    assert out["forwarded"]["syslog"] >= 1
    stream, frames = b"".join(chunks), []
    while stream:
        length, _, rest = stream.partition(b" ")
        frames.append(rest[: int(length)])
        stream = rest[int(length) :]
    assert len(frames) == out["forwarded"]["syslog"]
    assert frames[0].startswith(b"<") and b" z-server z-audit - " in frames[0]
    assert json.loads(frames[0].split(b" - ", 2)[2])["seq"] == 1


def test_forwarding_settings_are_validated_and_status_is_read_only(admin: TestClient) -> None:
    body = admin.get("/api/v1/admin/audit-settings").json()
    for forward in (
        {"syslog": {"enabled": True, "url": "https://localhost/x"}},
        {"https": {"enabled": True, "url": "http://localhost/x"}},
        {"https": {"enabled": False, "url": "", "token_ref": "plain-token-value"}},
        {"kafka": {"enabled": True, "url": "https://localhost/x"}},
    ):
        resp = admin.put("/api/v1/admin/audit-settings", json={**body, "forward": forward}, headers=csrf(admin))
        assert resp.status_code == 422, forward
    saved = _forwarding(
        admin,
        https={"enabled": False, "url": "https://localhost/z", "token_ref": "env:Z_AUDIT_TOKEN", "status": {"x": 1}},
    )
    assert "status" not in saved["forward"]["https"]


def test_verify_accepts_a_retention_purge_only_up_to_a_signed_checkpoint(admin: TestClient, database_url: str) -> None:
    _event(admin)
    cp = _tick(admin)["checkpoint"]
    _event(admin)
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as conn:  # the owner role, as the documented retention job
        conn.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only")
        conn.execute("DELETE FROM audit_events WHERE seq <= %s", (cp,))
        conn.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only")
    ok = admin.post("/api/v1/admin/audit:verify", json={}, headers=csrf(admin)).json()
    assert ok["ok"] is True and ok["checkpoints_verified"] == 1, ok
    with psycopg.connect(dsn, autocommit=True) as conn:  # removing one more event leaves the rest unanchored
        conn.execute("ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only")
        conn.execute("DELETE FROM audit_events WHERE seq = %s", (cp + 1,))
        conn.execute("ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only")
    bad = admin.post("/api/v1/admin/audit:verify", json={}, headers=csrf(admin)).json()
    assert bad["ok"] is False and "no signed checkpoint" in bad["detail"]
