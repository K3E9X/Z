"""Audit worker (docs/12 §4): signed checkpoints on a schedule and continuous forwarding of the log.

- Checkpoint: every `checkpoint_minutes`, if events were written since the last one, (seq, hash) of the chain
  head is signed with the server key. The checkpoint is itself an audit event, so forwarders carry it too.
- Forwarding: each enabled target keeps a cursor (the last forwarded seq) in `settings`; events after it are
  sent in order, in batches, and the cursor only moves once the target accepted the batch. Delivery is
  at-least-once: every line carries seq and hash, which a log platform can use to drop duplicates.
  - `syslog`: RFC 5424 over TLS (`tls://host:port`), octet-counted framing (RFC 6587 §3.4.1).
  - `https`: POST of JSON lines (`application/x-ndjson`); `token_ref` is a secret reference (env:, file:,
    vault:, kubernetes:) sent as a bearer token, never stored or shown as a value.
- With several z-server replicas one worker wins a transaction-scoped advisory lock per tick; the others skip.
Retention is not enforced here: the app's database role cannot delete audit rows (invariant 9).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from z_server import audit
from z_server.auth.identity import now
from z_server.db.models import AuditCheckpoint, AuditEvent, Setting
from z_server.errors import Problem
from z_server.gateway.secrets import SecretError, SecretResolver
from z_server.security import check_outbound_url
from z_server.state import AppState

log = logging.getLogger("z.audit")
LOCK_KEY = 0x5A_A0D18  # pg_try_advisory_xact_lock key for the audit worker (the chain uses 0x5A_A0D17)
SETTINGS_KEY = "audit_settings"
STATE_KEY = "audit_forward_state"
BATCH = 500
SEND_TIMEOUT_S = 10.0
TARGETS = ("syslog", "https")

Sender = Callable[[str, dict[str, Any], list[AuditEvent]], Awaitable[None]]


class ForwardError(Exception):
    pass


async def effective_settings(db: AsyncSession, config: dict[str, Any]) -> dict[str, Any]:
    cfg = config.get("audit", {})
    value: dict[str, Any] = {
        "retention_days": cfg.get("retention_days", 365),
        "checkpoint_minutes": cfg.get("checkpoint_minutes", 60),
        "forward": cfg.get("forward", {}),
    }
    row = await db.get(Setting, SETTINGS_KEY)
    if row is not None:
        value.update(row.value)
    return value


async def forward_state(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(Setting, STATE_KEY)
    return dict(row.value) if row is not None else {}


async def _save_state(db: AsyncSession, value: dict[str, Any]) -> None:
    row = await db.get(Setting, STATE_KEY)
    if row is None:
        db.add(Setting(key=STATE_KEY, value=value, updated_by="system"))
    else:
        row.value, row.updated_by = value, "system"


async def checkpoint_if_due(db: AsyncSession, state: AppState, minutes: int) -> AuditCheckpoint | None:
    last = (
        await db.execute(select(AuditCheckpoint).order_by(AuditCheckpoint.seq.desc()).limit(1))
    ).scalar_one_or_none()
    head = (await db.execute(select(AuditEvent.seq).order_by(AuditEvent.seq.desc()).limit(1))).scalar_one_or_none()
    if head is None:
        return None
    if last is not None:
        if head <= last.seq + 1:  # only the previous checkpoint's own event was written since
            return None
        if last.ts + timedelta(minutes=max(1, minutes)) > now():
            return None
    return await audit.checkpoint(db, state.signer)


# ---------------------------------------------------------------- senders


def syslog_frame(row: AuditEvent, hostname: str) -> bytes:
    line = audit.syslog_line(row, hostname).encode()
    return str(len(line)).encode() + b" " + line


async def send_syslog(url: str, target: dict[str, Any], rows: list[AuditEvent]) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "tls" or not parsed.hostname:
        raise ForwardError("syslog forwarding needs a tls://host:port URL")
    context = ssl.create_default_context()  # honours SSL_CERT_FILE for an internal CA
    hostname = str(target.get("hostname") or "z-server")
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname, parsed.port or 6514, ssl=context), SEND_TIMEOUT_S
        )
    except (OSError, TimeoutError) as exc:
        raise ForwardError(f"cannot reach {parsed.hostname}: {type(exc).__name__}") from exc
    try:
        writer.write(b"".join(syslog_frame(r, hostname) for r in rows))
        await asyncio.wait_for(writer.drain(), SEND_TIMEOUT_S)
    except (OSError, TimeoutError) as exc:
        raise ForwardError(f"syslog write failed: {type(exc).__name__}") from exc
    finally:
        writer.close()
        with contextlib.suppress(OSError, TimeoutError, ssl.SSLError):
            await asyncio.wait_for(writer.wait_closed(), 2)


def https_sender(client: httpx.AsyncClient, resolver: SecretResolver) -> Sender:
    async def send(url: str, target: dict[str, Any], rows: list[AuditEvent]) -> None:
        if urlparse(url).scheme != "https":
            raise ForwardError("HTTPS forwarding needs an https:// URL")
        headers = {"Content-Type": "application/x-ndjson"}
        if target.get("token_ref"):
            try:
                headers["Authorization"] = "Bearer " + await resolver.resolve(str(target["token_ref"]))
            except SecretError as exc:
                raise ForwardError("the forwarding token is unavailable") from exc
        body = "".join(audit.canonical_json(audit.export_line(r)) + "\n" for r in rows)
        try:
            resp = await client.post(url, content=body, headers=headers, timeout=SEND_TIMEOUT_S, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise ForwardError(f"cannot reach the log endpoint: {type(exc).__name__}") from exc
        if not 200 <= resp.status_code < 300:
            raise ForwardError(f"the log endpoint answered HTTP {resp.status_code}")

    return send


# ---------------------------------------------------------------- one tick


async def forward(db: AsyncSession, name: str, target: dict[str, Any], sender: Sender, status: dict[str, Any]) -> int:
    """Send the events after this target's cursor. Returns how many were accepted."""
    entry = dict(status.get(name) or {})
    url = str(target.get("url") or "")
    if entry.get("url") != url:  # a new destination starts from now, not from the beginning of the log
        head = (await db.execute(select(AuditEvent.seq).order_by(AuditEvent.seq.desc()).limit(1))).scalar()
        entry = {"url": url, "last_seq": int(head or 0) if target.get("from_now", True) else 0}
    sent = 0
    try:
        await check_outbound_url(url)
        while True:
            rows = list(
                (
                    await db.execute(
                        select(AuditEvent)
                        .where(AuditEvent.seq > int(entry["last_seq"]))
                        .order_by(AuditEvent.seq)
                        .limit(BATCH)
                    )
                ).scalars()
            )
            if not rows:
                break
            await sender(url, target, rows)
            entry["last_seq"], sent = rows[-1].seq, sent + len(rows)
            entry["last_sent_at"] = now().isoformat()
            if len(rows) < BATCH:
                break
        entry["last_error"] = None
    except (ForwardError, Problem) as exc:
        entry["last_error"] = str(exc.detail if isinstance(exc, Problem) else exc)[:300]
        entry["last_error_at"] = now().isoformat()
        log.warning("audit forwarding to %s failed: %s", name, entry["last_error"])
    status[name] = entry
    return sent


async def tick(state: AppState, senders: dict[str, Sender] | None = None) -> dict[str, Any]:
    resolver = state.gateway.resolver if state.gateway else SecretResolver(state.http)
    senders = senders or {"syslog": send_syslog, "https": https_sender(state.http, resolver)}
    out: dict[str, Any] = {"checkpoint": None, "forwarded": {}}
    # The checkpoint takes the chain lock: its own short transaction, no network I/O while it is held.
    async with state.sessions() as db, db.begin():
        if not (await db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY})).scalar():
            return {"skipped": True}
        settings = await effective_settings(db, state.config)
        cp = await checkpoint_if_due(db, state, int(settings.get("checkpoint_minutes") or 60))
        out["checkpoint"] = cp.seq if cp else None
    async with state.sessions() as db, db.begin():
        if not (await db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": LOCK_KEY})).scalar():
            return {**out, "skipped": True}
        status = await forward_state(db)
        for name in TARGETS:
            target = (settings.get("forward") or {}).get(name)
            if isinstance(target, dict) and target.get("enabled") and target.get("url"):
                out["forwarded"][name] = await forward(db, name, target, senders[name], status)
        await _save_state(db, status)
    return out


async def run_forever(state: AppState, interval_s: float = 15.0) -> None:
    while True:
        try:
            await tick(state)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("audit worker tick failed")
        await asyncio.sleep(interval_s)
