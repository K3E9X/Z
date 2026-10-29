"""Sign-in (GitLab OIDC, CLI exchange, local accounts), step-up, sign-out, /me (docs/12 §1)."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from z_common.models import api_identity as m

from z_server import audit
from z_server.api.framework import API_PREFIX, CSRF_COOKIE, SESSION_COOKIE, ContractRouter, Ctx, CtxDep
from z_server.api.views import session_out, token_out, user_out
from z_server.auth import gitlab, identity, local
from z_server.auth.oidc import OIDCClient, OIDCError
from z_server.crypto import new_token, pkce_pair, token_hash
from z_server.db.models import ApiToken, AuthSession, LoginState, Setting, User
from z_server.errors import Problem, problem_response
from z_server.ids import new_id

router = ContractRouter()
STATE_TTL = timedelta(minutes=10)
LOGIN_LIMIT = (10, 60.0)  # attempts per window (seconds), per IP and per username


def safe_redirect(target: str | None) -> str:
    """Only same-origin relative paths: no open redirects."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def callback_url(ctx: Ctx) -> str:
    return f"{ctx.state.settings.public_url}{API_PREFIX}/auth/callback"


def set_session_cookies(response: Response, token: str, csrf: str | None, max_age: int) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax")
    if csrf:
        response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, path="/", secure=True, httponly=False, samesite="lax")


def clear_session_cookies(response: Response) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=True, samesite="lax")


async def _begin_oidc(ctx: Ctx, purpose: str, redirect_to: str, session_id: str | None = None) -> str:
    auth = await ctx.state.auth_settings(ctx.db)
    if not auth.get("issuer_url") or not auth.get("client_id"):
        raise Problem("unavailable", "GitLab sign-in is not configured yet (Admin -> Sign-in)")
    client = await ctx.state.oidc(ctx.db)
    verifier, challenge = pkce_pair()
    row = LoginState(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=verifier,
        purpose=purpose,
        redirect_to=safe_redirect(redirect_to),
        session_id=session_id,
        expires_at=identity.now() + STATE_TTL,
    )
    ctx.db.add(row)
    max_age = int(auth.get("step_up_max_age_s", 300)) if purpose == "step_up" else None
    try:
        return await client.authorize_url(
            callback_url(ctx),
            row.state,
            row.nonce,
            challenge,
            list(auth.get("scopes", ["openid", "profile", "email"])),
            step_up_max_age=max_age,
        )
    except OIDCError as exc:
        raise Problem("unavailable", f"GitLab is not reachable ({exc.reason})") from exc


@router.operation("authLogin")
async def auth_login(ctx: CtxDep, request: Request, redirect: str | None = None) -> Response:
    if ctx.state.settings.auth_mode == "dev":
        return RedirectResponse(safe_redirect(redirect), status_code=302)
    url = await _begin_oidc(ctx, "login", redirect or "/")
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(m.LoginRedirect(redirect_url=url).dump())
    return RedirectResponse(url, status_code=302)


@router.operation("authCallback", status_code=302)
async def auth_callback(
    ctx: CtxDep, code: str | None = None, state: str | None = None, error: str | None = None
) -> Response:
    async def fail(reason: str, detail: str = "") -> Response:
        await ctx.audit("auth.login", "user", None, result="denied", after={"reason": reason, "detail": detail[:300]})
        return RedirectResponse(f"/signin?error={reason}", status_code=302)

    if error:
        return await fail("provider_error", error)
    login_state = await ctx.db.get(LoginState, state or "") if state else None
    if login_state is None or not code:
        return await fail("invalid_state")
    await ctx.db.delete(login_state)  # single use: a replayed state is refused
    if login_state.expires_at <= identity.now():
        return await fail("expired_state")
    client = await ctx.state.oidc(ctx.db)
    try:
        tokens = await client.exchange_code(code, callback_url(ctx), login_state.code_verifier)
        min_auth = login_state.created_at.timestamp() if login_state.purpose == "step_up" else None
        claims = await client.validate_id_token(
            str(tokens.get("id_token", "")), login_state.nonce, min_auth_time=min_auth
        )
    except OIDCError as exc:
        return await fail(exc.reason, exc.detail)

    if login_state.purpose == "step_up":
        session = await ctx.db.get(AuthSession, login_state.session_id or "")
        user = await ctx.db.get(User, session.user_id) if session else None
        if session is None or user is None or user.sub != str(claims["sub"]) or session.revoked_at is not None:
            return await fail("step_up_mismatch")
        session.step_up_at = identity.now()
        await audit.record(
            ctx.db,
            audit.Actor(user.username, ctx.ip, ctx.request.headers.get("user-agent"), session.id, ctx.request_id),
            "auth.step_up",
            "session",
            session.id,
        )
        ctx.audited = True
        return RedirectResponse(login_state.redirect_to or "/", status_code=302)

    auth = await ctx.state.auth_settings(ctx.db)
    try:
        result = await gitlab.sign_in(ctx.db, client, auth, claims, tokens, ctx.state.sealer)
    except OIDCError as exc:
        return await fail(exc.reason, exc.detail)
    lifetimes = await ctx.state.lifetimes(ctx.db)
    token, session = await identity.create_session(
        ctx.db, result.user, "web", lifetimes, ip=ctx.ip, user_agent=ctx.request.headers.get("user-agent"), step_up=True
    )
    actor = audit.Actor(result.user.username, ctx.ip, ctx.request.headers.get("user-agent"), session.id, ctx.request_id)
    await audit.record(
        ctx.db,
        actor,
        "auth.login",
        "user",
        result.user.id,
        after={"method": "gitlab", "created": result.created, "groups": result.user.groups},
    )
    ctx.audited = True
    if result.is_admin and await gitlab.disable_bootstrap(ctx.db):
        await audit.record(ctx.db, actor, "auth.bootstrap_disabled", "local_account", local.BOOTSTRAP_NAME)
    response = RedirectResponse(login_state.redirect_to or "/", status_code=302)
    set_session_cookies(response, token, session.csrf_token, int(lifetimes.absolute.total_seconds()))
    return response


@router.operation("authLogout")
async def auth_logout(ctx: CtxDep) -> Response:
    if ctx.user.session_id:
        session = await ctx.db.get(AuthSession, ctx.user.session_id)
        if session and session.revoked_at is None:
            session.revoked_at, session.revoked_reason = identity.now(), "logout"
    await ctx.audit("auth.logout", "session", ctx.user.session_id)
    response = Response(status_code=204)
    clear_session_cookies(response)
    return response


@router.operation("authStepUp")
async def auth_step_up(ctx: CtxDep, body: m.StepUpRequest | None = None) -> Response:
    body = body or m.StepUpRequest()
    principal = ctx.user
    if principal.kind == "dev":
        await ctx.audit("auth.step_up", "session", None, after={"method": "dev"})
        return JSONResponse(m.StepUpResponse(step_up_at=identity.now()).dump())
    if principal.local_kind in {"bootstrap", "test", "breakglass"}:
        user = await ctx.db.get(User, principal.user_id)
        name = user.sub if user else ""
        policy = ctx.state.config_auth.get("local_accounts", {}).get("password_policy", {})
        try:
            await local.login(ctx.db, name, body.password or "", body.totp, policy, ctx.state.sealer)
        except local.LocalLoginError as exc:
            await ctx.audit(
                "auth.step_up", "session", principal.session_id, result="denied", after={"reason": exc.reason}
            )
            return problem_response(Problem("unauthenticated", "re-authentication failed"), ctx.request)
        session = await ctx.db.get(AuthSession, principal.session_id or "")
        if session is None:
            raise Problem("unauthenticated")
        session.step_up_at = identity.now()
        await ctx.audit("auth.step_up", "session", session.id, after={"method": "local"})
        return JSONResponse(m.StepUpResponse(step_up_at=session.step_up_at).dump())
    if principal.session_kind != "web" or not principal.session_id:
        raise Problem("forbidden", "step-up needs a web session; sign in from the browser")
    url = await _begin_oidc(ctx, "step_up", body.return_to or "/", principal.session_id)
    await ctx.audit("auth.step_up_started", "session", principal.session_id)
    return JSONResponse(m.StepUpResponse(redirect_url=url).dump())


@router.operation("authCliConfig")
async def auth_cli_config(ctx: CtxDep) -> m.CliAuthConfig:
    auth = await ctx.state.auth_settings(ctx.db)
    cli = auth.get("cli", {})
    return m.CliAuthConfig(
        mode=ctx.state.settings.auth_mode,
        issuer_url=str(auth.get("issuer_url", "")),
        client_id=str(auth.get("cli_client_id") or auth.get("client_id", "")),
        scopes=list(auth.get("scopes", ["openid", "profile", "email"])),
        device_flow=bool(cli.get("device_flow", True)),
        loopback_redirect=str(cli.get("loopback_redirect", "http://127.0.0.1:8765/callback")),
    )


@router.operation("authCliExchange")
async def auth_cli_exchange(ctx: CtxDep, body: m.CliExchangeRequest) -> Response:
    if not ctx.state.limiter.allow(f"cli:{ctx.ip}", *LOGIN_LIMIT):
        raise Problem("rate-limited")
    lifetimes = await ctx.state.lifetimes(ctx.db)
    if body.dev:
        if ctx.state.settings.auth_mode != "dev" or ctx.state.settings.production:
            raise Problem("forbidden", "dev exchange is only available in auth.mode dev")
        from z_server.auth.dev import dev_user

        user = await dev_user(ctx.db, ctx.state)
    else:
        if not body.gitlab_access_token:
            raise Problem("validation", "gitlab_access_token is required")
        auth = await ctx.state.auth_settings(ctx.db)
        client = await ctx.state.oidc(ctx.db)
        try:
            info = await client.userinfo(body.gitlab_access_token)
            claims: dict[str, Any] = {
                "sub": info.get("sub"),
                "iss": client.issuer,
                "preferred_username": info.get("preferred_username"),
            }
            if body.id_token:
                audience = str(auth.get("cli_client_id") or auth.get("client_id", ""))
                checked = await OIDCClient(client.issuer, audience, "", ctx.state.http).validate_id_token(
                    body.id_token, None
                )
                if checked.get("sub") != claims["sub"]:
                    raise OIDCError("subject_mismatch", "ID token and access token belong to different users")
            result = await gitlab.sign_in(
                ctx.db, client, auth, claims, {"access_token": body.gitlab_access_token}, ctx.state.sealer
            )
        except OIDCError as exc:
            await ctx.audit("auth.cli_login", "user", None, result="denied", after={"reason": exc.reason})
            return problem_response(Problem("unauthenticated", f"GitLab sign-in refused ({exc.reason})"), ctx.request)
        user = result.user
    token, session = await identity.create_session(
        ctx.db,
        user,
        "cli",
        lifetimes,
        device=body.device_name[:255],
        ip=ctx.ip,
        user_agent=ctx.request.headers.get("user-agent"),
    )
    # the route is public: the actor is the user who just signed in, not the anonymous caller
    await audit.record(
        ctx.db,
        audit.Actor(
            user.username,
            ip=ctx.ip,
            user_agent=ctx.request.headers.get("user-agent"),
            session_id=session.id,
            request_id=ctx.request_id,
        ),
        "auth.cli_token_issued",
        "session",
        session.id,
        after={"device": session.device},
    )
    ctx.audited = True
    return JSONResponse(
        m.CliToken(token=token, expires_at=session.expires_at, user=await user_out(ctx.db, ctx.state, user)).dump(),
        status_code=200,
    )


@router.operation("authLocalLogin")
async def auth_local_login(ctx: CtxDep, body: m.LocalLoginRequest) -> Response:
    if not (
        ctx.state.limiter.allow(f"local:{ctx.ip}", *LOGIN_LIMIT)
        and ctx.state.limiter.allow(f"local-user:{body.username}", *LOGIN_LIMIT)
    ):
        await ctx.audit(
            "auth.local_login", "local_account", body.username, result="denied", after={"reason": "rate_limited"}
        )
        return problem_response(Problem("rate-limited"), ctx.request)
    policy = ctx.state.config_auth.get("local_accounts", {}).get("password_policy", {})
    try:
        user = await local.login(ctx.db, body.username, body.password, body.totp, policy, ctx.state.sealer)
    except local.LocalLoginError as exc:
        # committed with the request: failure counters and lockout persist
        await ctx.audit(
            "auth.local_login", "local_account", body.username, result="denied", after={"reason": exc.reason}
        )
        return problem_response(Problem("unauthenticated", "sign-in refused"), ctx.request)
    lifetimes = await ctx.state.lifetimes(ctx.db)
    token, session = await identity.create_session(
        ctx.db, user, "web", lifetimes, ip=ctx.ip, user_agent=ctx.request.headers.get("user-agent"), step_up=True
    )
    await audit.record(
        ctx.db,
        audit.Actor(user.username, ctx.ip, ctx.request.headers.get("user-agent"), session.id, ctx.request_id),
        "auth.local_login",
        "local_account",
        body.username,
        after={"kind": user.local_kind},
    )
    ctx.audited = True
    if user.local_kind == "breakglass":
        from z_server import notifications

        notifications.enqueue(ctx.db, "breakglass_used", payload={"ip": ctx.ip})
    principal = await identity.build_principal(
        ctx.db, user, ctx.state.config_auth, kind=user.local_kind or "local", session=session
    )
    response = JSONResponse((await _me(ctx, principal_user=user, principal=principal, session=session)).dump())
    set_session_cookies(response, token, session.csrf_token, int(lifetimes.absolute.total_seconds()))
    return response


BANNERS = {
    "bootstrap": "Signed in with the bootstrap account: configure GitLab sign-in and the admin group mapping, then sign in with GitLab.",
    "test": "Signed in with a shared test account. Not for production data.",
    "breakglass": "Break-glass session: every action is audited and all admins are alerted.",
}


async def _me(ctx: Ctx, *, principal_user: User, principal: Any, session: AuthSession | None) -> m.Me:
    banners = []
    if principal_user.local_kind in BANNERS:
        banners.append(
            m.Banner(
                kind="breakglass" if principal_user.local_kind == "breakglass" else "local_account",
                text=BANNERS[principal_user.local_kind],
            )
        )
    workspace = ctx.state.config.get("workspace", {})
    expires = session.expires_at if session else identity.now() + timedelta(hours=12)
    return m.Me(
        user=await user_out(ctx.db, ctx.state, principal_user),
        permissions=[
            m.PermissionGrant(permission=g.permission, scope=g.scope)
            for g in sorted(principal.grants, key=lambda g: (g.permission, g.scope))
        ],
        session=m.SessionInfo(
            id=session.id if session else "dev",
            kind=(session.kind if session else "web"),
            expires_at=expires,
            step_up_at=session.step_up_at if session else None,
        ),
        banners=banners,
        csrf_token=session.csrf_token if session else None,
        workspace=m.WorkspaceInfo(name=str(workspace.get("name", "Z")), environment=workspace.get("environment")),
    )


@router.operation("getMe")
async def get_me(ctx: CtxDep) -> m.Me:
    user = await ctx.db.get(User, ctx.user.user_id)
    if user is None:
        raise Problem("unauthenticated")
    session = await ctx.db.get(AuthSession, ctx.user.session_id) if ctx.user.session_id else None
    return await _me(ctx, principal_user=user, principal=ctx.user, session=session)


@router.operation("listMySessions")
async def list_my_sessions(ctx: CtxDep) -> list[m.AuthSession]:
    rows = (
        await ctx.db.execute(
            select(AuthSession)
            .where(AuthSession.user_id == ctx.user.user_id, AuthSession.revoked_at.is_(None))
            .order_by(AuthSession.created_at.desc())
        )
    ).scalars()
    return [session_out(r, ctx.user.username, ctx.user.session_id) for r in rows if r.expires_at > identity.now()]


@router.operation("revokeMySession", status_code=204)
async def revoke_my_session(ctx: CtxDep, id: str) -> Response:
    row = await ctx.db.get(AuthSession, id)
    if row is None or row.user_id != ctx.user.user_id:
        raise Problem("not-found")
    row.revoked_at, row.revoked_reason = identity.now(), "revoked by the user"
    await ctx.audit("auth.session_revoked", "session", id)
    return Response(status_code=204)


@router.operation("listMyTokens")
async def list_my_tokens(ctx: CtxDep) -> list[m.ApiToken]:
    rows = (
        await ctx.db.execute(
            select(ApiToken).where(ApiToken.user_id == ctx.user.user_id).order_by(ApiToken.created_at.desc())
        )
    ).scalars()
    return [token_out(r, ctx.user.username) for r in rows]


@router.operation("createMyToken", status_code=201)
async def create_my_token(ctx: CtxDep, body: m.ApiTokenCreate) -> m.ApiToken:
    max_days = int(ctx.state.config_auth.get("api_tokens", {}).get("max_days", 90))
    if body.expires_in_days > max_days:
        raise Problem("validation", f"tokens expire after at most {max_days} days")
    held = {g.permission for g in ctx.user.grants}
    missing = sorted(set(body.scopes) - held)
    if missing:
        raise Problem("forbidden", f"you cannot grant scopes you do not hold: {', '.join(missing)}")
    token = new_token(identity.API_TOKEN_PREFIX)
    row = ApiToken(
        id=new_id("tok"),
        user_id=ctx.user.user_id,
        name=body.name,
        scopes=sorted(set(body.scopes)),
        token_hash=token_hash(token),
        expires_at=identity.now() + timedelta(days=body.expires_in_days),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    await ctx.audit(
        "auth.api_token_created",
        "api_token",
        row.id,
        after={"name": row.name, "scopes": row.scopes, "expires_at": row.expires_at},
    )
    return token_out(row, ctx.user.username, token)


@router.operation("revokeMyToken", status_code=204)
async def revoke_my_token(ctx: CtxDep, id: str) -> Response:
    row = await ctx.db.get(ApiToken, id)
    if row is None or row.user_id != ctx.user.user_id:
        raise Problem("not-found")
    row.revoked_at = identity.now()
    await ctx.audit("auth.api_token_revoked", "api_token", id)
    return Response(status_code=204)


# ---------------------------------------------------------------- my notifications (ADR 0023)


async def _my_notifications(ctx: Ctx) -> m.MyNotifications:
    from z_server import notifications

    prefs = await notifications.personal_prefs(ctx.db, ctx.user.user_id)
    user = await ctx.db.get(User, ctx.user.user_id)
    smtp = (await notifications.settings(ctx.db))["smtp"]
    return m.MyNotifications.model_validate(
        {"email": prefs, "address": user.email if user else None, "available": bool(smtp)}
    )


@router.operation("getMyNotifications")
async def get_my_notifications(ctx: CtxDep) -> m.MyNotifications:
    return await _my_notifications(ctx)


@router.operation("putMyNotifications")
async def put_my_notifications(ctx: CtxDep, body: m.MyNotifications) -> m.MyNotifications:
    from z_server import notifications

    before = await notifications.personal_prefs(ctx.db, ctx.user.user_id)
    after = body.email.model_dump(mode="json")
    after["events"] = sorted(set(after["events"]))
    key = notifications.PERSONAL_KEY + ctx.user.user_id
    row = await ctx.db.get(Setting, key)
    if row is None:
        ctx.db.add(Setting(key=key, value=after, updated_by=ctx.user.username))
    else:
        row.value, row.updated_by = after, ctx.user.username
    await ctx.db.flush()
    await ctx.audit("me.notifications_changed", "user", ctx.user.user_id, before=before, after=after)
    return await _my_notifications(ctx)
