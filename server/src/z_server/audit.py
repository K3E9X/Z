"""Tamper-evident audit log (docs/12 §4).

hash = sha256(prev_hash || canonical_json(event)), where the event excludes prev_hash and hash.
Appends take a transaction-scoped advisory lock so the chain is linear; they run in the SAME transaction as
the change they describe. Hourly checkpoints sign (seq, hash) with the server's Ed25519 key.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from z_server.crypto import AuditSigner
from z_server.db.models import AuditCheckpoint, AuditEvent

GENESIS = "0" * 64
LOCK_KEY = 0x5A_A0D17  # pg_advisory_xact_lock key for the audit chain
FIELDS = (
    "seq",
    "ts",
    "actor",
    "actor_ip",
    "user_agent",
    "session_id",
    "action",
    "target_type",
    "target_id",
    "team",
    "result",
    "runs_as",
    "before",
    "after",
    "request_id",
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def event_dict(row: AuditEvent) -> dict[str, Any]:
    data = {f: getattr(row, f) for f in FIELDS}
    data["ts"] = _ts(row.ts)
    return data


def compute_hash(prev_hash: str, event: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(event)).encode()).hexdigest()


@dataclass
class Actor:
    name: str  # username, local:<name>, system, agent:<slug>
    ip: str | None = None
    user_agent: str | None = None
    session_id: str | None = None
    request_id: str | None = None


RESULTS = frozenset({"success", "denied", "error"})  # contracts: AuditEvent.result


async def record(
    db: AsyncSession,
    actor: Actor,
    action: str,
    target_type: str,
    target_id: str | None = None,
    *,
    result: str = "success",
    team: str | None = None,
    runs_as: str | None = None,
    before: Any = None,
    after: Any = None,
) -> AuditEvent:
    if result not in RESULTS:
        raise ValueError(f"audit result must be one of {sorted(RESULTS)}, not {result!r}")
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
    last = (await db.execute(select(AuditEvent.seq, AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))).first()
    seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)
    row = AuditEvent(
        seq=seq,
        ts=datetime.now(UTC),
        actor=actor.name,
        actor_ip=actor.ip,
        user_agent=actor.user_agent,
        session_id=actor.session_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        team=team,
        result=result,
        runs_as=runs_as,
        before=_jsonable(before),
        after=_jsonable(after),
        request_id=actor.request_id,
        prev_hash=prev,
        hash="",
    )
    row.hash = compute_hash(prev, event_dict(row))
    db.add(row)
    await db.flush()
    return row


def _jsonable(value: Any) -> Any:
    return None if value is None else json.loads(canonical_json(value))


@dataclass
class Verification:
    ok: bool
    checked: int
    first_bad_seq: int | None
    checkpoints_verified: int
    detail: str


async def verify(db: AsyncSession, signer: AuditSigner, batch: int = 5000) -> Verification:
    prev, expected_seq, checked = GENESIS, 1, 0
    heads: dict[int, str] = {}
    last_seq = 0
    while True:
        rows = (
            (
                await db.execute(
                    select(AuditEvent).where(AuditEvent.seq > last_seq).order_by(AuditEvent.seq).limit(batch)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            break
        for row in rows:
            if row.seq != expected_seq:
                return Verification(False, checked, row.seq, 0, f"gap: expected seq {expected_seq}, found {row.seq}")
            if row.prev_hash != prev or compute_hash(prev, event_dict(row)) != row.hash:
                return Verification(False, checked, row.seq, 0, f"hash mismatch at seq {row.seq}")
            heads[row.seq] = row.hash
            prev, expected_seq, checked, last_seq = row.hash, row.seq + 1, checked + 1, row.seq
    verified = 0
    for cp in (await db.execute(select(AuditCheckpoint).order_by(AuditCheckpoint.seq))).scalars():
        if heads.get(cp.seq) != cp.hash or not signer.verify(f"{cp.seq}:{cp.hash}", cp.signature):
            return Verification(False, checked, cp.seq, verified, f"checkpoint {cp.seq} does not match the chain")
        verified += 1
    return Verification(True, checked, None, verified, "chain and checkpoints verified")


async def checkpoint(db: AsyncSession, signer: AuditSigner) -> AuditCheckpoint | None:
    last = (await db.execute(select(AuditEvent.seq, AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1))).first()
    if not last:
        return None
    existing = await db.get(AuditCheckpoint, last[0])
    if existing:
        return existing
    cp = AuditCheckpoint(seq=last[0], hash=last[1], signature=signer.sign(f"{last[0]}:{last[1]}"), key_id=signer.key_id)
    db.add(cp)
    await record(
        db,
        Actor("system"),
        "audit.checkpoint",
        "audit_checkpoint",
        str(last[0]),
        after={"seq": last[0], "hash": last[1]},
    )
    return cp


def export_line(row: AuditEvent) -> dict[str, Any]:
    data = event_dict(row)
    data["prev_hash"], data["hash"] = row.prev_hash, row.hash
    return data


def syslog_line(row: AuditEvent, hostname: str = "z-server") -> str:
    """RFC 5424 line with the event as structured JSON message."""
    severity = 4 if row.result != "success" else 6
    pri = 10 * 8 + severity  # facility authpriv (10)
    return f"<{pri}>1 {_ts(row.ts)} {hostname} z-audit - {row.action} - {canonical_json(export_line(row))}"
