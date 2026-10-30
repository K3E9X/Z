"""Typed connector secrets (ADR 0033): a secret an admin types in the web app for a connector's read or write
service credential, sealed with the platform key on that credential row.

- Stored: `credentials.sealed_value` (AES-GCM, associated data bound to the row id), `backend` = `sealed`,
  `ref` = `sealed:<connector>/<purpose>` (a non-secret marker), `rotated_at` = when it was typed.
- Used: the credential injection (`gateway.auth.material`) asks the resolver for the typed secret of the purpose it
  needs, so the read secret only ever reaches agent calls and the write secret only the action executor.
- Never returned, logged, audited or put in an event: the audit records connector, purpose, who and a fingerprint.
- Cleared: the row goes back to the manifest's reference (or disappears when the manifest has none).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from z_server.db.models import Connector, Credential
from z_server.gateway.secrets import SealedLookup, TypedSecret
from z_server.state import AppState

BACKEND = "sealed"
PURPOSES = ("read", "write")


def marker(connector: str, purpose: str) -> str:
    return f"{BACKEND}:{connector}/{purpose}"


def _aad(credential_id: str) -> str:
    return f"connector-secret:{credential_id}"


def seal(state: AppState, credential: Credential, value: str) -> bytes:
    return state.sealer.seal(value, _aad(credential.id))


def unseal(state: AppState, credential: Credential) -> str:
    if credential.sealed_value is None:
        raise ValueError("credential has no typed secret")
    return state.sealer.open(credential.sealed_value, _aad(credential.id))


def fingerprint(state: AppState, value: str) -> str:
    """A keyed, truncated digest to tell two typed values apart in the audit log; it reveals nothing of the value."""
    key = hashlib.sha256(f"{state.settings.secret_key}:typed-secret-fingerprint".encode()).digest()
    return "hmac-sha256:" + hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:16]


async def service_row(db: AsyncSession, connector: Connector, purpose: str, *, lock: bool = False) -> Credential | None:
    stmt = select(Credential).where(
        Credential.connector_id == connector.id, Credential.scope == "service", Credential.purpose == purpose
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalars().first()


def is_typed(credential: Credential) -> bool:
    return credential.backend == BACKEND and credential.sealed_value is not None


def lookup(state: AppState) -> SealedLookup:
    """The resolver's lookup: the typed secret of a connector's service credential for one purpose."""

    async def find(connector: str, purpose: str) -> TypedSecret | None:
        if purpose not in PURPOSES:
            return None
        async with state.sessions() as db:
            row = (
                (
                    await db.execute(
                        select(Credential)
                        .join(Connector, Connector.id == Credential.connector_id)
                        .where(
                            Connector.name == connector,
                            Credential.scope == "service",
                            Credential.purpose == purpose,
                            Credential.backend == BACKEND,
                            Credential.sealed_value.is_not(None),
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is None:
                return None
            return TypedSecret(ref=row.ref, value=unseal(state, row), username=row.identity)

    return find


def now() -> datetime:
    return datetime.now(UTC)
