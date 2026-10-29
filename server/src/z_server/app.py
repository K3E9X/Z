"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.routing import Route

from z_server import __version__, errors
from z_server.api import (
    routes_admin,
    routes_audit,
    routes_auth,
    routes_catalog,
    routes_channels,
    routes_connections,
    routes_connectors,
    routes_intake,
    routes_internal,
    routes_learning,
    routes_missions,
    routes_objectives,
    routes_review,
    routes_sessions,
    routes_usage,
)
from z_server.crypto import AuditSigner, Sealer
from z_server.db.session import make_engine, make_sessionmaker
from z_server.events import EventHub
from z_server.gateway.mcp_server import PATH as GATEWAY_PATH
from z_server.gateway.mcp_server import GatewayEndpoint
from z_server.llm.litellm import KeyAdmin, LiteLLMKeyAdmin
from z_server.security import SecurityHeaders
from z_server.settings import Settings
from z_server.startup import prepare
from z_server.state import AppState

log = logging.getLogger("z.server")
ROUTERS = (
    routes_auth.router,
    routes_admin.router,
    routes_audit.router,
    routes_catalog.router,
    routes_channels.router,
    routes_connections.router,
    routes_connectors.router,
    routes_missions.router,
    routes_review.router,
    routes_internal.router,
    routes_usage.router,
    routes_intake.router,
    routes_learning.router,
    routes_objectives.router,
    routes_sessions.router,
)


def create_app(
    settings: Settings,
    *,
    http: httpx.AsyncClient | None = None,
    run_startup: bool = True,
    llm_keys: KeyAdmin | None = None,
    workers: bool = False,
) -> FastAPI:
    """`workers=True` (z-server all) also runs the scheduler and the identity re-evaluation in this process."""
    settings.check()
    gateway_endpoint = GatewayEndpoint(None)  # type: ignore[arg-type]  # bound to the Gateway in the lifespan

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        client = http or httpx.AsyncClient(timeout=20, follow_redirects=False)
        state = AppState(
            settings=settings,
            engine=engine,
            sessions=make_sessionmaker(engine),
            http=client,
            sealer=Sealer(settings.secret_key),
            signer=AuditSigner(settings.secret_key),
            llm_keys=llm_keys or LiteLLMKeyAdmin(settings.litellm_url, settings.litellm_master_key, client),
            hub=EventHub(settings.database_url),
        )
        app.state.z = state
        from z_server.gateway.core import Gateway

        state.gateway = Gateway.create(state)
        gateway_endpoint.gateway = state.gateway
        await state.hub.start()
        if run_startup:
            await prepare(state)
        tasks: list[asyncio.Task[None]] = []
        if workers:
            from z_server.executors.factory import from_settings
            from z_server.gateway import executor
            from z_server.workers import channels, health, identity, intake, learning, scheduler

            tasks = [
                asyncio.create_task(scheduler.run_forever(state, from_settings(settings)), name="scheduler"),
                asyncio.create_task(identity.run_forever(state), name="identity"),
                asyncio.create_task(executor.run_forever(state), name="executor"),
                asyncio.create_task(health.run_forever(state), name="health"),
                asyncio.create_task(intake.run_forever(state), name="intake"),
                asyncio.create_task(learning.run_forever(state), name="learning"),
                asyncio.create_task(channels.run_forever(state), name="channels"),
            ]
        try:
            async with gateway_endpoint.run():
                yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if http is None:
                await client.aclose()
            await state.hub.stop()
            await engine.dispose()

    app = FastAPI(
        title="Z",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.production else "/api/openapi.json",
    )
    errors.install(app)
    app.router.routes.append(Route(GATEWAY_PATH, endpoint=gateway_endpoint, methods=["GET", "POST", "DELETE"]))
    app.add_middleware(SecurityHeaders)
    for router in ROUTERS:
        app.include_router(router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> JSONResponse:
        state: AppState = app.state.z
        try:
            async with state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 — reported as not ready
            log.warning("database not ready: %s", exc)
            return JSONResponse(
                {"status": "down", "version": __version__, "checks": {"database": {"ok": False}}}, status_code=503
            )
        return JSONResponse({"status": "ok", "version": __version__, "checks": {"database": {"ok": True}}})

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> PlainTextResponse:
        # Runners can reach z-server: metrics need Z_METRICS_TOKEN as a bearer token (required in production).
        token = os.environ.get("Z_METRICS_TOKEN", "")
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if (token and not hmac.compare_digest(given, token)) or (not token and settings.production):
            return PlainTextResponse("not found", status_code=404)
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    return app
