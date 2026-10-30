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
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from playwright.async_api import Browser, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, ConfigDict, Field
from z_common.models.manifests import BrowserFlow

from z_browser.flows import Credentials, replay, scrub
from z_browser.record import Recorder, RecordError

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


class RecordStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = None
    allowed_hosts: list[str] | None = None
    start_url: str
    auth: Auth | None = None


class RecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["click", "type", "credential", "press", "goto", "extract", "assert", "screenshot", "undo"]
    x: float | None = None
    y: float | None = None
    text: Annotated[str, Field(max_length=4000)] | None = None
    arg: str | None = None
    field: Literal["username", "password"] | None = None
    key: str | None = None
    url: Annotated[str, Field(max_length=2000)] | None = None
    what: Literal["text", "table", "link"] | None = None
    name: str | None = None


class Runtime:
    def __init__(self) -> None:
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.slots = asyncio.Semaphore(int(os.environ.get("Z_BROWSER_CONCURRENCY", "4")))
        self.lock = asyncio.Lock()
        self.recorder = Recorder(int(os.environ.get("Z_BROWSER_RECORDINGS", "2")))

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

    def _recording(rec_id: str) -> Any:
        try:
            return runtime.recorder.get(rec_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="no such recording (it may have expired)") from exc

    @app.post("/v1/record", dependencies=[Depends(authorized)])
    async def record_start(body: RecordStart) -> dict[str, Any]:
        creds = Credentials(**body.auth.model_dump()) if body.auth else Credentials()
        try:
            rec = await runtime.recorder.start(
                await runtime.chromium(), body.base_url, body.allowed_hosts, body.start_url, creds
            )
        except RecordError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        log.info("recording started")
        return await rec.state()

    @app.get("/v1/record/{rec_id}", dependencies=[Depends(authorized)])
    async def record_state(rec_id: str) -> dict[str, Any]:
        rec = _recording(rec_id)
        async with rec.lock:
            return await rec.state()  # type: ignore[no-any-return]

    @app.post("/v1/record/{rec_id}/input", dependencies=[Depends(authorized)])
    async def record_input(rec_id: str, body: RecordInput) -> dict[str, Any]:
        rec = _recording(rec_id)
        async with rec.lock:
            try:
                await rec.act(body.model_dump(exclude_none=True))
            except (RecordError, PlaywrightError) as exc:
                message = scrub(str(exc).strip().splitlines()[0][:300], rec.creds.secrets())
                raise HTTPException(status_code=422, detail=message) from exc
            return await rec.state()  # type: ignore[no-any-return]

    @app.post("/v1/record/{rec_id}/finish", dependencies=[Depends(authorized)])
    async def record_finish(rec_id: str) -> dict[str, Any]:
        rec = _recording(rec_id)
        async with rec.lock:
            flow = rec.flow()
            blocked = list(rec.blocked)
        await runtime.recorder.close(rec_id)
        return {"flow": flow, "blocked_hosts": blocked}

    @app.delete("/v1/record/{rec_id}", dependencies=[Depends(authorized)], status_code=204)
    async def record_cancel(rec_id: str) -> None:
        await runtime.recorder.close(rec_id)

    return app
