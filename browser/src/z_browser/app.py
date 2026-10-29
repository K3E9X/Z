"""The z-browser HTTP API, reached only by the Z gateway and executor (bearer `Z_BROWSER_TOKEN`).

One Chromium per worker, one fresh context per run (no cookie or storage shared between runs or users), at most
`Z_BROWSER_CONCURRENCY` runs at a time. The worker never logs arguments, credentials or page content.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from playwright.async_api import Browser, Playwright, async_playwright
from pydantic import BaseModel, ConfigDict, Field
from z_common.models.manifests import BrowserFlow

from z_browser.flows import Credentials, replay

log = logging.getLogger("z.browser")


class Auth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    headers: dict[str, str] = Field(default_factory=dict)
    username: str | None = None
    password: str | None = None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flow: BrowserFlow
    base_url: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    auth: Auth | None = None
    timeout_s: Annotated[int, Field(ge=5, le=300)] | None = None


class Runtime:
    def __init__(self) -> None:
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.slots = asyncio.Semaphore(int(os.environ.get("Z_BROWSER_CONCURRENCY", "4")))
        self.lock = asyncio.Lock()

    async def chromium(self) -> Browser:
        async with self.lock:
            if self.browser is None or not self.browser.is_connected():
                if self.playwright is None:
                    self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(headless=True)
            return self.browser

    async def close(self) -> None:
        if self.browser is not None:
            await self.browser.close()
        if self.playwright is not None:
            await self.playwright.stop()


def create_app(token: str | None = None) -> FastAPI:
    secret = token if token is not None else os.environ.get("Z_BROWSER_TOKEN", "")
    if len(secret) < 16:
        raise RuntimeError("Z_BROWSER_TOKEN must be set (at least 16 characters)")
    runtime = Runtime()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await runtime.close()

    app = FastAPI(title="z-browser", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(authorization: Annotated[str | None, Header()] = None) -> None:
        presented = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(presented.encode(), secret.encode()):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/run", dependencies=[Depends(authorized)])
    async def run(body: RunRequest) -> dict[str, Any]:
        creds = Credentials(**body.auth.model_dump()) if body.auth else Credentials()
        async with runtime.slots:
            browser = await runtime.chromium()
            result = await replay(browser, body.flow, body.base_url, body.args, creds, body.timeout_s)
        log.info("flow finished ok=%s steps=%d blocked=%d", result.ok, len(result.steps), len(result.blocked_hosts))
        return asdict(result)

    return app
