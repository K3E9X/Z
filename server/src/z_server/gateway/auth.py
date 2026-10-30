"""Credential injection (docs/05 §4): manifest `auth.read` for the gateway, `auth.write` for the executor.

Read and write are always different credentials (invariant 7); a connector without a write credential cannot
apply actions. Values come from the secrets backends, or from a secret typed in the web app and sealed with the
platform key (ADR 0033), and only ever exist in request headers.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from z_common.config import substitute

from z_server.gateway.secrets import SecretError, SecretResolver

Purpose = Literal["read", "write"]
TYPED_KINDS = frozenset({"bearer", "api_key", "basic"})  # static secrets: the kinds a typed secret can replace


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
    other_purpose: Purpose = "write" if purpose == "read" else "read"
    other = auth_config(manifest, other_purpose)
    name = str(manifest.get("name") or "")
    # ADR 0033: a secret typed in the web app replaces the manifest reference of this purpose only
    typed = await resolver.typed(name, purpose) if kind in TYPED_KINDS else None
    other_typed = await resolver.typed(name, other_purpose) if typed is not None and other else None
    mine = typed.ref if typed else auth.get("secret")
    theirs = other_typed.ref if other_typed else (other or {}).get("secret")
    if (mine and mine == theirs) or (typed and other_typed and typed.value == other_typed.value):
        raise AuthError("read and write credentials must be different secrets (invariant 7)")
    if kind not in TYPED_KINDS:
        raise AuthError(f"auth type {kind!r} is not supported by this gateway build")
    if typed is not None:
        secret = typed.value
        if typed.username:
            auth = {**auth, "username": typed.username}
    else:
        ref = auth.get("secret")
        if not ref:
            raise AuthError(f"{purpose} credential has no secret reference and no typed secret")
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
