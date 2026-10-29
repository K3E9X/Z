"""Personal connections (docs/05 §6, Settings → Connections): a person's delegated OAuth grants, one per
purpose (read app, write app), used only in missions that person starts or triggers they own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.crypto import new_token, token_hash
from z_server.db.models import Connector, Credential, PersonalConnection, Setting
from z_server.errors import Problem
from z_server.gateway import oauth
from z_server.ids import new_id

router = ContractRouter()
STATE_TTL = timedelta(minutes=10)
STATE_PREFIX = "oauth_state:"
SETTINGS_PAGE = "/settings/connections"


def connection_out(row: PersonalConnection, connector: str) -> dict[str, Any]:
    return {
        "id": row.id,
        "connector": connector,
        "status": row.status,
        "read_scopes": list(row.read_scopes or []),
        "write_scopes": list(row.write_scopes or []),
        "last_used_at": row.last_used_at,
        "created_at": row.created_at,
    }


def _redirect_uri(ctx: Ctx) -> str:
    return f"{ctx.state.settings.public_url}/api/v1/oauth/callback"


@router.operation("startOAuth")
async def start_oauth(ctx: CtxDep, connector: str, body: dict[str, Any]) -> dict[str, Any]:
    row = (await ctx.db.execute(select(Connector).where(Connector.name == connector))).scalar_one_or_none()
    if row is None or not row.enabled or row.review_status != "approved":
        raise Problem("not-found", f"no connector {connector} to connect to")
    purpose = str(body["purpose"])  # read | write (validated against the contract)
    verifier, challenge = oauth.pkce()
    state = new_token("zo", 24)
    try:
        url = oauth.authorize_url(row.manifest, purpose, state, challenge, _redirect_uri(ctx))
    except oauth.OAuthError as exc:
        raise Problem("validation", str(exc)) from exc
    ctx.db.add(
        Setting(
            key=STATE_PREFIX + token_hash(state),
            value={
                "user_id": ctx.user.user_id,
                "connector": row.name,
                "purpose": purpose,
                "verifier": ctx.state.sealer.seal(verifier, f"pkce:{token_hash(state)}").hex(),
                "expires_at": (datetime.now(UTC) + STATE_TTL).isoformat(),
            },
        )
    )
    await ctx.audit("connection.started", "connector", row.id, after={"purpose": purpose})
    return {"redirect_url": url}


def _back(error: str | None = None, connected: str | None = None) -> RedirectResponse:
    query = f"?error={quote(error)}" if error else (f"?connected={quote(connected)}" if connected else "")
    return RedirectResponse(SETTINGS_PAGE + query, status_code=302)


@router.operation("oauthCallback", status_code=302)
async def oauth_callback(
    ctx: CtxDep, code: str | None = None, state: str | None = None, error: str | None = None
) -> Response:
    pending = await ctx.db.get(Setting, STATE_PREFIX + token_hash(state), with_for_update=True) if state else None
    if pending is None or state is None:
        await ctx.audit("connection.failed", "connector", None, result="denied", after={"reason": "unknown state"})
        return _back("this sign-in link expired or was already used")
    value = dict(pending.value)
    await ctx.db.delete(pending)  # single use, whatever happens next
    if datetime.fromisoformat(value["expires_at"]) < datetime.now(UTC):
        await ctx.audit("connection.failed", "connector", None, result="error", after={"reason": "state expired"})
        return _back("this sign-in link expired")
    if ctx.principal is not None and ctx.principal.user_id != value["user_id"]:
        await ctx.audit("connection.failed", "connector", None, result="denied", after={"reason": "user mismatch"})
        return _back("signed in as someone else than who started the connection")
    connector = (
        await ctx.db.execute(select(Connector).where(Connector.name == value["connector"]))
    ).scalar_one_or_none()
    if connector is None:
        await ctx.audit("connection.failed", "connector", None, result="error", after={"reason": "connector gone"})
        return _back("the connector no longer exists")
    if error or not code:
        await ctx.audit(
            "connection.failed", "connector", connector.id, result="error", after={"reason": (error or "no code")[:200]}
        )
        return _back(f"the provider refused: {error or 'no code'}")
    purpose = str(value["purpose"])
    verifier = ctx.state.sealer.open(bytes.fromhex(value["verifier"]), f"pkce:{token_hash(state)}")
    resolver = ctx.state.gateway.resolver if ctx.state.gateway else None
    if resolver is None:
        from z_server.gateway.secrets import SecretResolver

        resolver = SecretResolver(ctx.state.http)
    try:
        tokens = await oauth.exchange(
            ctx.state.http, resolver, connector.manifest, purpose, code, verifier, _redirect_uri(ctx)
        )
    except oauth.OAuthError as exc:
        await ctx.audit(
            "connection.failed", "connector", connector.id, result="error", after={"reason": str(exc)[:300]}
        )
        return _back("the token exchange failed")
    user_id = str(value["user_id"])
    conn = (
        await ctx.db.execute(
            select(PersonalConnection).where(
                PersonalConnection.user_id == user_id, PersonalConnection.connector_id == connector.id
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        conn = PersonalConnection(id=new_id("pcn"), user_id=user_id, connector_id=connector.id, status="active")
        ctx.db.add(conn)
        await ctx.db.flush()
    old_id = conn.read_credential_id if purpose == "read" else conn.write_credential_id
    identity = oauth.identity_of(tokens)
    other_id = conn.write_credential_id if purpose == "read" else conn.read_credential_id
    other = await ctx.db.get(Credential, other_id) if other_id else None
    if identity and other is not None and other.identity and other.identity != identity:
        # a mission would read one account and write as another: both grants must be the same account
        await ctx.audit(
            "connection.failed",
            "connector",
            connector.id,
            result="denied",
            after={"reason": "account mismatch", "purpose": purpose},
        )
        return _back(
            f"the {purpose} connection used {identity}, the other one {other.identity}: connect both with the same "
            "account (revoke the connection first to switch accounts)"
        )
    granted = str(tokens.get("scope") or " ".join(oauth.scopes_for(connector.manifest, purpose))).split()
    cred = Credential(
        id=new_id("crd"),
        connector_id=connector.id,
        purpose=purpose,
        scope="user",
        owner_user_id=user_id,
        backend="sealed",
        ref="",
        identity=identity,
        scopes=granted,
        expires_at=oauth.expiry(tokens),
    )
    cred.ref = oauth.seal(ctx.state, cred.id, tokens)
    ctx.db.add(cred)
    await ctx.db.flush()
    if purpose == "read":
        conn.read_credential_id, conn.read_scopes = cred.id, granted
    else:
        conn.write_credential_id, conn.write_scopes = cred.id, granted
    conn.status = "active"
    if old_id:
        old = await ctx.db.get(Credential, old_id)
        if old is not None:
            await ctx.db.delete(old)
    await ctx.audit(
        "connection.created", "connector", connector.id, runs_as=user_id, after={"purpose": purpose, "scopes": granted}
    )
    return _back(connected=connector.name)


@router.operation("listMyConnections")
async def list_my_connections(ctx: CtxDep) -> list[dict[str, Any]]:
    rows = (
        await ctx.db.execute(
            select(PersonalConnection, Connector.name)
            .join(Connector, Connector.id == PersonalConnection.connector_id)
            .where(PersonalConnection.user_id == ctx.user.user_id)
            .order_by(PersonalConnection.created_at)
        )
    ).all()
    return [connection_out(c, name) for c, name in rows]


@router.operation("revokeMyConnection", status_code=204)
async def revoke_my_connection(ctx: CtxDep, id: str) -> Response:
    conn = await ctx.db.get(PersonalConnection, id, with_for_update=True)
    if conn is None or conn.user_id != ctx.user.user_id:
        raise Problem("not-found", "no such connection")
    for cred_id in (conn.read_credential_id, conn.write_credential_id):
        cred = await ctx.db.get(Credential, cred_id) if cred_id else None
        if cred is not None:
            await ctx.db.delete(cred)  # the sealed tokens are gone with it
    conn.read_credential_id = conn.write_credential_id = None
    conn.status = "revoked"
    await ctx.audit("connection.revoked", "connector", conn.connector_id)
    return Response(status_code=204)
