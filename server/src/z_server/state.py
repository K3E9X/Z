"""Process-wide state shared by every request: settings, database, HTTP client, crypto, OIDC client."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from z_server.auth.identity import Lifetimes
from z_server.auth.oidc import OIDCClient
from z_server.crypto import AuditSigner, Sealer
from z_server.db.models import Setting
from z_server.events import EventHub
from z_server.llm.litellm import KeyAdmin
from z_server.settings import Settings

AUTH_SETTINGS_KEY = "auth_settings"
SECRET_AAD = "oidc-client-secret"  # noqa: S105 (AEAD label)


class RateLimiter:
    """Sliding-window limiter, in process memory (per replica)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        at = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] <= at - window_s:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(at)
        return True


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    sealer: Sealer
    signer: AuditSigner
    llm_keys: KeyAdmin
    hub: EventHub
    limiter: RateLimiter = field(default_factory=RateLimiter)
    _oidc: OIDCClient | None = None
    _oidc_key: tuple[str, str, str] | None = None

    @property
    def config(self) -> dict[str, Any]:
        return self.settings.config

    @property
    def config_auth(self) -> dict[str, Any]:
        auth: dict[str, Any] = self.settings.config.get("auth", {})
        return auth

    async def auth_settings(self, db: AsyncSession) -> dict[str, Any]:
        """config/z.yaml auth, overlaid by Admin -> Sign-in edits (secret kept sealed)."""
        merged = dict(self.config_auth)
        merged["client_secret"] = self.settings.oidc_client_secret or str(merged.get("client_secret", ""))
        row = await db.get(Setting, AUTH_SETTINGS_KEY)
        if row is not None:
            for key in (
                "issuer_url",
                "client_id",
                "cli_client_id",
                "scopes",
                "groups_claim",
                "session",
                "step_up_max_age_s",
            ):
                if key in row.value:
                    merged[key] = row.value[key]
            if row.value.get("client_secret_sealed"):
                merged["client_secret"] = self.sealer.open(bytes.fromhex(row.value["client_secret_sealed"]), SECRET_AAD)
        return merged

    async def oidc(self, db: AsyncSession) -> OIDCClient:
        auth = await self.auth_settings(db)
        key = (str(auth.get("issuer_url", "")), str(auth.get("client_id", "")), str(auth.get("client_secret", "")))
        if self._oidc is None or self._oidc_key != key:
            self._oidc = OIDCClient(key[0], key[1], key[2], self.http)
            self._oidc_key = key
        return self._oidc

    async def lifetimes(self, db: AsyncSession) -> Lifetimes:
        return Lifetimes.from_config(await self.auth_settings(db))

    async def step_up_max_age(self, db: AsyncSession) -> int:
        return int((await self.auth_settings(db)).get("step_up_max_age_s", 300))
