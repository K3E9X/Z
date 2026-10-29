"""OAuth credentials (docs/05 §6): delegated user grants (authorization code + PKCE) and app-only service tokens
(client credentials), with read and write always on different app registrations (invariant 7).

- A person connects from Settings → Connections once per purpose (read app, write app). Tokens are sealed in
  `credentials.ref` (AES-GCM, associated data = the credential id) and never leave z-server.
- The gateway uses the read token of the mission starter when a step runs with `credentials: user`; the
  executor uses the write token. Tokens are refreshed shortly before they expire.
- Service tokens (client credentials) are cached in memory until shortly before they expire. Without
  configured scopes, the app-only scope is `<API origin>/.default` (the Microsoft identity platform convention).
- Endpoints come from the connector's `oauth.authorize_url` / `token_url`, or from `oauth.tenant_id` for the
  Microsoft identity platform.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from sqlalchemy import select

from z_server.db.models import Connector, Credential, PersonalConnection
from z_server.gateway.secrets import SecretError, SecretResolver
from z_server.state import AppState

ACTING_USER: ContextVar[str | None] = ContextVar("z_acting_user", default=None)  # user-scope calls only
REFRESH_MARGIN_S = 120
MICROSOFT = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/{endpoint}"


class OAuthError(RuntimeError):
    pass


def endpoints(manifest: dict[str, Any]) -> tuple[str, str]:
    cfg = manifest.get("oauth") or {}
    authorize, token = cfg.get("authorize_url"), cfg.get("token_url")
    if (not authorize or not token) and cfg.get("tenant_id"):
        tenant = str(cfg["tenant_id"])
        authorize = authorize or MICROSOFT.format(tenant=tenant, endpoint="authorize")
        token = token or MICROSOFT.format(tenant=tenant, endpoint="token")
    if not authorize or not token:
        raise OAuthError(f"connector {manifest.get('name')} has no OAuth endpoints")
    return str(authorize), str(token)


def flow(manifest: dict[str, Any], purpose: str, scope: str) -> dict[str, Any] | None:
    auth = (manifest.get("auth") or {}).get(purpose)
    if not isinstance(auth, dict) or "type" in auth:
        return None
    found = auth.get(scope)
    return dict(found) if isinstance(found, dict) else None


def app_config(manifest: dict[str, Any], app: str) -> dict[str, Any]:
    cfg = ((manifest.get("oauth") or {}).get(app)) or {}
    if not cfg.get("client_id"):
        raise OAuthError(f"OAuth app {app} has no client_id")
    return dict(cfg)


async def _client(resolver: SecretResolver, manifest: dict[str, Any], app: str) -> tuple[str, str | None]:
    cfg = app_config(manifest, app)
    secret = None
    if cfg.get("secret"):
        try:
            secret = await resolver.resolve(str(cfg["secret"]))
        except SecretError as exc:
            raise OAuthError(f"OAuth client secret unavailable: {exc}") from exc
    return str(cfg["client_id"]), secret


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def scopes_for(manifest: dict[str, Any], purpose: str) -> list[str]:
    f = flow(manifest, purpose, "user") or {}
    return [str(s) for s in f.get("scopes") or []]


def authorize_url(manifest: dict[str, Any], purpose: str, state: str, challenge: str, redirect_uri: str) -> str:
    f = flow(manifest, purpose, "user")
    if f is None or f.get("flow") != "authorization_code_pkce":
        raise OAuthError(f"connector {manifest.get('name')} has no delegated {purpose} flow")
    authorize, _ = endpoints(manifest)
    client_id = app_config(manifest, str(f["app"]))["client_id"]
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join([*scopes_for(manifest, purpose), "offline_access"]),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return f"{authorize}?{urlencode(query)}"


async def _token_request(http: httpx.AsyncClient, url: str, form: dict[str, str]) -> dict[str, Any]:
    try:
        resp = await http.post(url, data=form, headers={"Accept": "application/json"}, timeout=20)
    except httpx.HTTPError as exc:
        raise OAuthError(f"token endpoint unreachable ({type(exc).__name__})") from exc
    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200 or not body.get("access_token"):
        raise OAuthError(
            f"token endpoint refused ({resp.status_code}): {body.get('error', '')} {body.get('error_description', '')[:200]}"
        )
    return body


async def exchange(
    http: httpx.AsyncClient,
    resolver: SecretResolver,
    manifest: dict[str, Any],
    purpose: str,
    code: str,
    verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    f = flow(manifest, purpose, "user") or {}
    client_id, secret = await _client(resolver, manifest, str(f.get("app")))
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
        "scope": " ".join([*scopes_for(manifest, purpose), "offline_access"]),
    }
    if secret:
        form["client_secret"] = secret
    return await _token_request(http, endpoints(manifest)[1], form)


async def refresh(
    http: httpx.AsyncClient, resolver: SecretResolver, manifest: dict[str, Any], purpose: str, refresh_token: str
) -> dict[str, Any]:
    f = flow(manifest, purpose, "user") or {}
    client_id, secret = await _client(resolver, manifest, str(f.get("app")))
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    if scopes := scopes_for(manifest, purpose):
        form["scope"] = " ".join([*scopes, "offline_access"])
    if secret:
        form["client_secret"] = secret
    return await _token_request(http, endpoints(manifest)[1], form)


# ---------------------------------------------------------------- stored delegated tokens


def seal(state: AppState, credential_id: str, tokens: dict[str, Any]) -> str:
    keep = {k: tokens.get(k) for k in ("access_token", "refresh_token", "token_type", "scope") if tokens.get(k)}
    return state.sealer.seal(json.dumps(keep), f"oauth:{credential_id}").hex()


def unseal(state: AppState, credential: Credential) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(state.sealer.open(bytes.fromhex(credential.ref), f"oauth:{credential.id}"))
    return data


def expiry(tokens: dict[str, Any]) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=int(tokens.get("expires_in") or 3600))


async def user_token(
    state: AppState, resolver: SecretResolver, connector: Connector, purpose: str, user_id: str
) -> tuple[str, str]:
    """(access token, identity) of `user_id`'s delegated grant for `purpose`, refreshed when needed. A grant
    that cannot be refreshed is marked expired (committed) before the error is raised."""
    failure: str | None = None
    result: tuple[str, str] | None = None
    async with state.sessions() as db, db.begin():
        conn = (
            await db.execute(
                select(PersonalConnection)
                .where(PersonalConnection.user_id == user_id, PersonalConnection.connector_id == connector.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        cred_id = (conn.read_credential_id if purpose == "read" else conn.write_credential_id) if conn else None
        cred = await db.get(Credential, cred_id, with_for_update=True) if cred_id else None
        if conn is None or conn.status != "active" or cred is None:
            raise OAuthError(f"the mission starter has no active {purpose} connection to {connector.name}")
        tokens = unseal(state, cred)
        due = cred.expires_at is None or cred.expires_at - timedelta(seconds=REFRESH_MARGIN_S) <= datetime.now(UTC)
        if due:
            try:
                if not tokens.get("refresh_token"):
                    raise OAuthError("no refresh token")
                fresh = await refresh(state.http, resolver, connector.manifest, purpose, str(tokens["refresh_token"]))
                tokens = {**tokens, **fresh}
                cred.ref, cred.expires_at, cred.rotated_at = (
                    seal(state, cred.id, tokens),
                    expiry(fresh),
                    datetime.now(UTC),
                )
            except OAuthError as exc:
                conn.status = "expired"
                failure = f"the {purpose} connection to {connector.name} expired ({exc}): connect again"
        if failure is None:
            conn.last_used_at = datetime.now(UTC)
            result = (str(tokens["access_token"]), str(cred.identity or user_id))
    if failure is not None or result is None:
        raise OAuthError(failure or "no token")
    return result


_service_cache: dict[tuple[str, str], tuple[float, str]] = {}


async def service_token(
    http: httpx.AsyncClient, resolver: SecretResolver, manifest: dict[str, Any], purpose: str
) -> str:
    f = flow(manifest, purpose, "service")
    if f is None or f.get("flow") != "client_credentials":
        raise OAuthError(f"connector {manifest.get('name')} has no app-only {purpose} flow")
    key = (str(manifest.get("name")), purpose)
    hit = _service_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    client_id, secret = await _client(resolver, manifest, str(f["app"]))
    base = urlparse(str(manifest.get("base_url") or ""))
    scope = " ".join(f.get("scopes") or []) or f"{base.scheme}://{base.netloc}/.default"
    form = {"grant_type": "client_credentials", "client_id": client_id, "scope": scope}
    if secret:
        form["client_secret"] = secret
    body = await _token_request(http, endpoints(manifest)[1], form)
    token = str(body["access_token"])
    _service_cache[key] = (time.monotonic() + max(60, int(body.get("expires_in") or 3600) - REFRESH_MARGIN_S), token)
    return token
