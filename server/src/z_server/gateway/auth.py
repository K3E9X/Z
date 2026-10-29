"""Credential injection (docs/05 §4): manifest `auth.read` for the gateway, `auth.write` for the executor.

Read and write are always different credentials (invariant 7); a connector without a write credential cannot
apply actions. Values come from the secrets backends and only ever exist in request headers.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from z_common.config import substitute

from z_server.gateway.secrets import SecretError, SecretResolver

Purpose = Literal["read", "write"]


class AuthError(RuntimeError):
    pass


@dataclass
class AuthMaterial:
    headers: dict[str, str] = field(default_factory=dict)
    identity: str | None = None
    secrets: list[str] = field(default_factory=list)  # values to redact from anything that leaves the gateway
    login: dict[str, str] = field(default_factory=dict)  # basic: username and password, for browser form logins


def auth_config(manifest: dict[str, Any], purpose: Purpose) -> dict[str, Any] | None:
    auth = (manifest.get("auth") or {}).get(purpose)
    if not isinstance(auth, dict):
        return None
    if "type" not in auth:  # scoped (user/service) OAuth flows
        return dict(auth.get("service") or {}) or None
    # non-secret fields (username = account e-mail, header names) may use ${ENV} placeholders
    return {k: (substitute(v, os.environ) if k in {"username", "header", "prefix"} else v) for k, v in auth.items()}


async def material(manifest: dict[str, Any], purpose: Purpose, resolver: SecretResolver) -> AuthMaterial:
    auth = auth_config(manifest, purpose)
    if auth is None:
        raise AuthError(f"connector {manifest.get('name')} has no {purpose} credential")
    kind = auth.get("type")
    if kind == "none":
        return AuthMaterial()
    other = auth_config(manifest, "write" if purpose == "read" else "read")
    if other and other.get("secret") and other.get("secret") == auth.get("secret"):
        raise AuthError("read and write credentials must be different secrets (invariant 7)")
    if kind not in {"bearer", "api_key", "basic"}:
        raise AuthError(f"auth type {kind!r} is not supported by this gateway build")
    ref = auth.get("secret")
    if not ref:
        raise AuthError(f"{purpose} credential has no secret reference")
    try:
        secret = await resolver.resolve(str(ref))
    except SecretError as exc:
        raise AuthError(f"{purpose} credential unavailable: {exc}") from exc
    if kind == "bearer":
        prefix = auth.get("prefix", "Bearer")
        header = auth.get("header", "Authorization")
        return AuthMaterial({header: f"{prefix} {secret}".strip()}, auth.get("username"), [secret])
    if kind == "api_key":
        header = auth.get("header", "X-API-Key")
        prefix = auth.get("prefix", "")
        return AuthMaterial({header: f"{prefix} {secret}".strip()}, auth.get("username"), [secret])
    username = str(auth.get("username") or "")
    if not username:
        raise AuthError("basic auth needs a username (for Jira Cloud: the account e-mail)")
    token = base64.b64encode(f"{username}:{secret}".encode()).decode()
    return AuthMaterial(
        {"Authorization": f"Basic {token}"}, username, [secret, token], {"username": username, "password": secret}
    )
