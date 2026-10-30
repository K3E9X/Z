"""One FastAPI app serving every fake tool, the MCP servers and the shared control API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.requests import Request as StarletteRequest
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .._mcp import McpEndpoint
from . import (
    botframework,
    cmdb,
    confluence,
    defectdojo,
    fwconsole,
    gitlab,
    google,
    graphql,
    jira,
    logos,
    m365,
    mattermost,
    recordedfuture,
    slack,
)
from .errors import FakeToolError
from .seed import default_seed
from .state import TOOLS, Store, extract_token


class BaseUrlMiddleware:
    """Remember the external base URL of the last request (self links, clone URLs)."""

    def __init__(self, app: ASGIApp, store: Store) -> None:
        self.app, self.store = app, store

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            self.store.base_url = str(StarletteRequest(scope).base_url).rstrip("/")
        await self.app(scope, receive, send)


def create_app(seed: dict[str, Any] | None = None, tokens: dict[str, dict[str, str]] | None = None) -> FastAPI:
    store = Store(seed or default_seed(), tokens)
    jira_api = jira.Jira(store)
    conf_api = confluence.Confluence(store)
    gl = gitlab.GitLab(store)
    store.load()

    def mcp(tool: str, tools: list[Any]) -> McpEndpoint:
        def authenticate(header: str | None) -> Any:
            return store.kind(
                tool, extract_token("gitlab" if tool == "gitlab" else "bearer", {"authorization": header or ""})
            )

        return McpEndpoint(f"fake-{tool}", tools, authenticate, lambda name, why: store.deny(tool, f"mcp {name}", why))

    endpoints = {
        "jira": mcp("jira", jira.mcp_tools(jira_api)),
        "gitlab": mcp("gitlab", gitlab.mcp_tools(gl)),
        "confluence": mcp("confluence", confluence.mcp_tools(conf_api)),
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            for endpoint in endpoints.values():
                await stack.enter_async_context(endpoint.run())
            yield
        gl.close()

    app = FastAPI(title="fake-tools", lifespan=lifespan)
    app.state.store = store
    app.state.gitlab = gl

    app.add_middleware(gitlab.GitLabPathMiddleware)
    app.add_middleware(BaseUrlMiddleware, store=store)

    @app.exception_handler(FakeToolError)
    async def tool_error(_request: Request, exc: FakeToolError) -> JSONResponse:
        return JSONResponse(exc.body or {"message": str(exc)}, status_code=exc.status)

    for name, endpoint in endpoints.items():
        app.router.routes.append(Route(f"/{name}/mcp", endpoint=endpoint, methods=["GET", "POST", "DELETE"]))

    app.include_router(defectdojo.router(store))
    app.include_router(cmdb.router(store))
    app.include_router(logos.router(store))
    app.include_router(m365.router(store))
    app.include_router(mattermost.router(store))
    app.include_router(slack.router(store))
    app.include_router(recordedfuture.router(store))
    for google_router in google.routers(store):
        app.include_router(google_router)
    app.include_router(botframework.router(store))
    app.include_router(fwconsole.router(store))
    app.include_router(graphql.router(store))
    app.include_router(jira.router(store, jira_api))
    app.include_router(jira.router_v3(store, jira_api))
    app.include_router(confluence.router(store, conf_api))
    app.include_router(gitlab.router(store, gl))

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "tools": list(TOOLS)}

    @app.post("/_fake/reset")
    def reset() -> dict[str, bool]:
        store.load()
        return {"ok": True}

    @app.post("/_fake/seed")
    async def seed_data(request: Request) -> JSONResponse:
        """Replace the data of each tool present in the body (other tools keep their data)."""
        body = await request.json()
        unknown = set(body) - set(TOOLS) - {"google"}  # google keeps its own state (OAuth grants)
        if unknown:
            return JSONResponse({"error": f"unknown tools {sorted(unknown)}"}, status_code=422)
        store.load(body, replace_all=False)
        return JSONResponse({"ok": True, "replaced": sorted(body)})

    @app.get("/_fake/state")
    def state() -> dict[str, Any]:
        return store.snapshot()

    @app.get("/_fake/data/{tool}")
    def data(tool: str) -> Any:
        return store.data.get(tool)

    @app.get("/_fake/tokens")
    def tokens_view() -> dict[str, dict[str, str]]:
        return store.tokens

    return app
