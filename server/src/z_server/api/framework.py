"""Contract-bound routing: path, method, permission, step-up and audit come from contracts/openapi.yaml.

    router = ContractRouter()

    @router.operation("getMe")
    async def get_me(ctx: CtxDep) -> Me: ...

The `Ctx` dependency authenticates, enforces the route permission (deny by default), the CSRF check for
cookie sessions, step-up, and local-account limits; it owns the request's database transaction and commits
before the response is sent. For `x-audit` routes it refuses to commit a successful request that wrote no
audit event, so "every mutating route writes an audit event" holds by construction.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.contracts import openapi

from z_server import audit
from z_server.auth import identity
from z_server.errors import Problem
from z_server.gateway.limits import replica_share
from z_server.rbac import Principal
from z_server.state import AppState

API_PREFIX = "/api/v1"
SPECIAL = {"public", "authenticated", "hmac", "job_token", "service"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# docs/12 §5: API requests per minute per session or token (per client IP when anonymous), shared out across
# replicas. Login and token endpoints have their own, stricter limits (routes_auth.LOGIN_LIMIT).
API_LIMIT_PER_MINUTE = 600
SESSION_COOKIE = "__Host-z_session"
CSRF_COOKIE = "__Host-z_csrf"
CSRF_HEADER = "X-CSRF-Token"

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


@dataclass(frozen=True)
class ContractOperation:
    operation_id: str
    method: str
    path: str
    permission: str
    step_up: bool
    audited: bool
    summary: str


@functools.cache
def contract_operations() -> dict[str, ContractOperation]:
    ops = {}
    for path, item in openapi()["paths"].items():
        for method, op in item.items():
            ops[op["operationId"]] = ContractOperation(
                operation_id=op["operationId"],
                method=method.upper(),
                path=API_PREFIX + path,
                permission=op["x-permission"],
                step_up=bool(op.get("x-step-up")),
                audited=bool(op.get("x-audit")),
                summary=op.get("summary", ""),
            )
    return ops


@dataclass
class Ctx:
    request: Request
    db: AsyncSession
    state: AppState
    op: ContractOperation
    principal: Principal | None
    request_id: str
    audited: bool = False
    after_commit: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    @property
    def user(self) -> Principal:
        if self.principal is None:
            raise Problem("unauthenticated")
        return self.principal

    @property
    def ip(self) -> str | None:
        return self.request.client.host if self.request.client else None

    def actor(self) -> audit.Actor:
        name = self.principal.username if self.principal else "anonymous"
        return audit.Actor(
            name=name,
            ip=self.ip,
            user_agent=self.request.headers.get("user-agent"),
            session_id=self.principal.session_id if self.principal else None,
            request_id=self.request_id,
        )

    async def audit(self, action: str, target_type: str, target_id: str | None = None, **kwargs: Any) -> None:
        await audit.record(self.db, self.actor(), action, target_type, target_id, **kwargs)
        self.audited = True

    def require(self, permission: str, team: str | None = None) -> None:
        """Resource-level check: a team's resource, or an org-level one (team None) that needs an org-wide grant."""
        if not self.user.can_on(permission, team):
            raise Problem(
                "forbidden", f"missing {permission}" + (f" for team {team}" if team else ""), permission=permission
            )


async def authenticate(request: Request, db: AsyncSession, state: AppState) -> Principal | None:
    """Bearer token (CLI session `zs_`, API token `zt_`) or session cookie; dev mode signs in the dev user."""
    lifetimes = await state.lifetimes(db)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token.startswith(identity.API_TOKEN_PREFIX + "_"):
            found_token = await identity.resolve_api_token(db, token)
            if found_token is None:
                raise Problem("unauthenticated", "invalid or expired token")
            row, user = found_token
            return await identity.build_principal(
                db, user, state.config_auth, kind="api_token", token_scopes=list(row.scopes)
            )
        found = await identity.resolve_session(db, token, lifetimes)
        if found is None or found[0].kind != "cli":
            raise Problem("unauthenticated", "invalid or expired token")
        session, user = found
        return await identity.build_principal(
            db, user, state.config_auth, kind=user.local_kind or "user", session=session
        )
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        found = await identity.resolve_session(db, cookie, lifetimes)
        if found is not None and found[0].kind == "web":
            session, user = found
            principal = await identity.build_principal(
                db, user, state.config_auth, kind=user.local_kind or "user", session=session
            )
            principal.via_cookie = True
            return principal
    if state.settings.auth_mode == "dev" and not state.settings.production:
        from z_server.auth.dev import dev_principal  # dev mode only

        return await dev_principal(db, state)
    return None


async def record_denial(
    state: AppState, request: Request, principal: Principal | None, op: ContractOperation, reason: str
) -> None:
    """Denied requests are audited in their own transaction (the request's one is rolled back)."""
    async with state.sessions() as db, db.begin():
        await audit.record(
            db,
            audit.Actor(
                name=principal.username if principal else "anonymous",
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                session_id=principal.session_id if principal else None,
                request_id=getattr(request.state, "request_id", None),
            ),
            "authz.denied",
            "route",
            f"{op.method} {op.path}",
            result="denied",
            after={"permission": op.permission, "reason": reason, "operation": op.operation_id},
        )


async def _ctx(request: Request) -> AsyncIterator[Ctx]:
    state: AppState = request.app.state.z
    route = request.scope.get("route")
    op_id = getattr(route, "operation_id", None)
    op = contract_operations().get(op_id or "")
    if op is None:
        raise Problem("internal", "route is not bound to a contract operation")
    async with state.sessions() as db:
        async with db.begin():
            if op.permission == "hmac":
                principal = None  # signed by the sender (platform signature or JWT), never a Z session or token
            else:
                try:
                    principal = await authenticate(request, db, state)
                except Problem:
                    if op.permission == "public":
                        principal = None
                    else:
                        raise
            client = request.client.host if request.client else "unknown"
            key = f"api:{principal.session_id or principal.user_id}" if principal else f"api-ip:{client}"
            if not state.limiter.allow(key, replica_share(API_LIMIT_PER_MINUTE), 60.0):
                raise Problem("rate-limited", f"more than {API_LIMIT_PER_MINUTE} requests a minute")
            denial = _check(op, principal, request, await state.step_up_max_age(db))
        if denial is not None:
            kind, reason = denial
            if kind == "forbidden":
                await record_denial(state, request, principal, op, reason)
            raise Problem(
                kind,
                reason,
                permission=op.permission if kind == "forbidden" else None,
                step_up_url=f"{API_PREFIX}/auth/step-up" if kind == "step-up-required" else None,
            )
        ctx = Ctx(request, db, state, op, principal, getattr(request.state, "request_id", ""))
        async with db.begin():
            yield ctx
            if op.audited and not ctx.audited:
                raise RuntimeError(f"{op.operation_id} is x-audit but wrote no audit event")
    for callback in ctx.after_commit:
        await callback()


def _check(
    op: ContractOperation, principal: Principal | None, request: Request, step_up_max_age: int
) -> tuple[str, str] | None:
    if op.permission in {"public", "hmac", "job_token", "service"}:
        return None
    if principal is None:
        return ("unauthenticated", "sign in first")
    if principal.local_kind == "bootstrap" and op.operation_id not in identity.BOOTSTRAP_OPERATIONS:
        return ("forbidden", "the bootstrap account can only configure sign-in and the group mapping")
    if op.permission != "authenticated" and not principal.can(op.permission):
        return ("forbidden", f"missing permission {op.permission}")
    if principal.via_cookie and request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if (
            not principal.csrf_token
            or not sent
            or sent != request.cookies.get(CSRF_COOKIE)
            or sent != principal.csrf_token
        ):
            return ("forbidden", "missing or invalid CSRF token")
    if op.step_up and principal.kind != "dev":
        at = principal.step_up_at
        if at is None or at + timedelta(seconds=step_up_max_age) < identity.now():
            return ("step-up-required", "re-authenticate to continue")
    return None


CtxDep = Annotated[Ctx, Depends(_ctx, scope="function")]


class ContractRouter(APIRouter):
    def operation(self, operation_id: str, **kwargs: Any) -> Callable[[F], F]:
        op = contract_operations()[operation_id]
        if op.permission in {"job_token", "service"}:
            raise ValueError(f"{operation_id}: internal routes use the internal router")

        def decorator(fn: F) -> F:
            status = kwargs.pop("status_code", None)
            self.add_api_route(
                op.path,
                fn,
                methods=[op.method],
                operation_id=operation_id,
                summary=op.summary,
                status_code=status,
                **kwargs,
            )
            return fn

        return decorator
