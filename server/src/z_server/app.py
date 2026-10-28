"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from z_server import __version__, errors
from z_server.api import routes_admin, routes_audit, routes_auth, routes_catalog, routes_connectors
from z_server.crypto import AuditSigner, Sealer
from z_server.db.session import make_engine, make_sessionmaker
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
    routes_connectors.router,
)


def create_app(settings: Settings, *, http: httpx.AsyncClient | None = None, run_startup: bool = True) -> FastAPI:
    settings.check()

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
        )
        app.state.z = state
        if run_startup:
            await prepare(state)
        try:
            yield
        finally:
            if http is None:
                await client.aclose()
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
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    return app
