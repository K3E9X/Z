"""Local accounts: bootstrap admin, generic test accounts, break-glass (docs/12 §1). Off by default."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from z_server.auth.identity import now
from z_server.crypto import Sealer, hash_password, verify_password
from z_server.db.models import LocalAccount, Team, User, UserTeam
from z_server.ids import new_id

LOCAL_ISSUER = "local"
BOOTSTRAP_NAME = "z-bootstrap"
BREAKGLASS_NAME = "z-breakglass"
TOTP_AAD = "breakglass-totp"


class LocalLoginError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class CreatedSecret:
    name: str
    password: str
    totp_uri: str | None = None


def parse_passwords(raw: str) -> dict[str, str]:
    """`tester=...;tester-approver=...` (Z_TEST_ACCOUNT_PASSWORDS)."""
    out = {}
    for pair in filter(None, (p.strip() for p in raw.split(";"))):
        name, _, password = pair.partition("=")
        out[name.strip()] = password
    return out


async def _user_for(db: AsyncSession, name: str, kind: str) -> User:
    user = (await db.execute(select(User).where(User.issuer == LOCAL_ISSUER, User.sub == name))).scalar_one_or_none()
    if user is None:
        user = User(
            id=new_id("usr"),
            issuer=LOCAL_ISSUER,
            sub=name,
            username=f"local:{name}",
            name=name,
            status="active",
            local_kind=kind,
        )
        db.add(user)
        await db.flush()
    return user


async def ensure_bootstrap(db: AsyncSession, config_auth: dict[str, Any]) -> CreatedSecret | None:
    """Create the bootstrap admin once. Returns the one-time password only when it is created now."""
    settings = config_auth.get("local_accounts", {}).get("bootstrap", {})
    if not settings.get("enabled", False):
        return None
    existing = (await db.execute(select(LocalAccount).where(LocalAccount.name == BOOTSTRAP_NAME))).scalar_one_or_none()
    if existing is not None:
        return None  # created before (maybe already disabled): never re-created implicitly
    return await _create_bootstrap(db, int(settings.get("ttl_hours", 24)))


async def reset_bootstrap(db: AsyncSession, ttl_hours: int = 24) -> CreatedSecret:
    """`z-server bootstrap --reset`: the only way to re-enable it."""
    existing = (await db.execute(select(LocalAccount).where(LocalAccount.name == BOOTSTRAP_NAME))).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.flush()
    return await _create_bootstrap(db, ttl_hours)


async def _create_bootstrap(db: AsyncSession, ttl_hours: int) -> CreatedSecret:
    password = secrets.token_urlsafe(18)
    user = await _user_for(db, BOOTSTRAP_NAME, "bootstrap")
    user.status = "active"
    db.add(
        LocalAccount(
            id=new_id("loc"),
            name=BOOTSTRAP_NAME,
            kind="bootstrap",
            user_id=user.id,
            password_hash=hash_password(password),
            role="admin",
            enabled=True,
            expires_at=now() + timedelta(hours=ttl_hours),
        )
    )
    await db.flush()
    return CreatedSecret(BOOTSTRAP_NAME, password)


async def ensure_test_accounts(
    db: AsyncSession, config_auth: dict[str, Any], passwords: dict[str, str], production: bool
) -> list[str]:
    accounts = config_auth.get("local_accounts", {}).get("test_accounts", []) or []
    if production and accounts:
        raise RuntimeError("test accounts are refused when Z_ENV=production")
    names = []
    teams = {t.name: t.id for t in (await db.execute(select(Team))).scalars()}
    for spec in accounts:
        name = spec["name"]
        if spec["role"] not in {"viewer", "operator", "approver"}:
            raise RuntimeError(f"test account {name}: role {spec['role']} is not allowed")
        password = passwords.get(name)
        if not password:
            continue  # no password configured: the account stays absent
        user = await _user_for(db, name, "test")
        row = (await db.execute(select(LocalAccount).where(LocalAccount.name == name))).scalar_one_or_none()
        if row is None:
            row = LocalAccount(
                id=new_id("loc"), name=name, kind="test", user_id=user.id, password_hash=hash_password(password)
            )
            row.expires_at = now() + timedelta(days=int(spec.get("expires_days", 30)))
            db.add(row)
        elif not verify_password(row.password_hash, password):
            row.password_hash = hash_password(password)
        row.role, row.team, row.enabled = spec["role"], spec.get("team"), True
        if spec.get("team") in teams:
            link = await db.get(UserTeam, (user.id, teams[spec["team"]]))
            if link is None:
                db.add(UserTeam(user_id=user.id, team_id=teams[spec["team"]], source="manual"))
        names.append(name)
    await db.flush()
    return names


async def ensure_breakglass(db: AsyncSession, enabled: bool, sealer: Sealer) -> CreatedSecret | None:
    row = (await db.execute(select(LocalAccount).where(LocalAccount.name == BREAKGLASS_NAME))).scalar_one_or_none()
    if not enabled:
        if row is not None:
            row.enabled = False
        return None
    password = secrets.token_urlsafe(24)
    totp_secret = pyotp.random_base32()
    user = await _user_for(db, BREAKGLASS_NAME, "breakglass")
    if row is None:
        row = LocalAccount(id=new_id("loc"), name=BREAKGLASS_NAME, kind="breakglass", user_id=user.id, password_hash="")
        db.add(row)
    # A new one-time password and TOTP seed at every start with break-glass on (printed once).
    row.password_hash = hash_password(password)
    row.totp_secret_enc = sealer.seal(totp_secret, TOTP_AAD)
    row.enabled, row.role, row.failed_attempts, row.locked_until = True, "admin", 0, None
    await db.flush()
    uri = pyotp.TOTP(totp_secret).provisioning_uri(name=BREAKGLASS_NAME, issuer_name="Z break-glass")
    return CreatedSecret(BREAKGLASS_NAME, password, uri)


async def login(
    db: AsyncSession, username: str, password: str, totp: str | None, policy: dict[str, Any], sealer: Sealer
) -> User:
    """Check a local login. Failures are counted; the account locks after `lockout_after` failures."""
    row = (
        await db.execute(select(LocalAccount).where(LocalAccount.name == username).with_for_update())
    ).scalar_one_or_none()
    at = now()
    if row is None:
        verify_password(hash_password("x"), password)  # same cost as a real check
        raise LocalLoginError("unknown_account")
    if not row.enabled:
        raise LocalLoginError("account_disabled")
    if row.expires_at is not None and row.expires_at <= at:
        row.enabled = False
        raise LocalLoginError("account_expired")
    if row.locked_until is not None and row.locked_until > at:
        raise LocalLoginError("account_locked")
    ok = verify_password(row.password_hash, password)
    if ok and row.kind == "breakglass":
        secret = sealer.open(row.totp_secret_enc or b"", TOTP_AAD) if row.totp_secret_enc else ""
        ok = bool(totp) and bool(secret) and pyotp.TOTP(secret).verify(str(totp), valid_window=1)
    if not ok:
        row.failed_attempts += 1
        if row.failed_attempts >= int(policy.get("lockout_after", 5)):
            row.locked_until = at + timedelta(minutes=int(policy.get("lockout_minutes", 15)))
            row.failed_attempts = 0
            raise LocalLoginError("account_locked")
        raise LocalLoginError("bad_credentials")
    row.failed_attempts, row.locked_until = 0, None
    user = await db.get(User, row.user_id) if row.user_id else None
    if user is None:
        raise LocalLoginError("unknown_account")
    user.last_login_at = at
    return user
