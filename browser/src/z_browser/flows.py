"""Replay one reviewed browser flow in a fresh browser context (docs/05 §1, ADR 0016).

Every request the page makes goes through a route guard: only http(s) to the connector's host and the flow's
`allowed_hosts`; everything else is aborted and reported. Credential headers are added only to requests to the
connector's own host, never to a CDN or another allowed host. Password fields are masked in screenshots, and
credential values are scrubbed from every message returned.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser, Locator, Page, Request, Route
from playwright.async_api import Error as PlaywrightError
from z_common.connectors.browser import FlowError, host_allowed, hosts_for, render, resolve_url
from z_common.models.manifests import BrowserFlow, BrowserStep

DEFAULT_STEP_TIMEOUT_MS = 15_000
DEFAULT_FLOW_TIMEOUT_S = 60
MAX_SCREENSHOTS = 5
MAX_TABLE_ROWS = 1000
MAX_TEXT_CHARS = 200_000
MASK = "input[type=password]"

TABLE_JS = """(t, max) => {
  const rows = [...t.querySelectorAll('tr')];
  if (!rows.length) return [];
  const head = [...rows[0].querySelectorAll('th,td')].map((c, i) => c.innerText.trim() || 'col' + (i + 1));
  return rows.slice(1, max + 1).map((r) => {
    const cells = [...r.querySelectorAll('td,th')].map((c) => c.innerText.trim());
    return Object.fromEntries(head.map((h, i) => [h, cells[i] ?? '']));
  });
}"""


@dataclass
class Credentials:
    headers: dict[str, str] = field(default_factory=dict)
    username: str | None = None
    password: str | None = None

    def fields(self) -> dict[str, str]:
        return {k: v for k, v in (("username", self.username), ("password", self.password)) if v is not None}

    def secrets(self) -> list[str]:
        values = [v for v in self.headers.values() if v]
        values += [v.split(" ", 1)[1] for v in self.headers.values() if " " in v]
        if self.password:
            values.append(self.password)
        return sorted({v for v in values if len(v) >= 4}, key=len, reverse=True)


@dataclass
class StepLog:
    index: int
    action: str
    ok: bool
    ms: int
    error: str | None = None


@dataclass
class FlowResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    url: str | None = None
    title: str | None = None
    screenshots: list[dict[str, str]] = field(default_factory=list)
    steps: list[StepLog] = field(default_factory=list)
    blocked_hosts: list[str] = field(default_factory=list)
    error: str | None = None


class StepFailed(Exception):
    pass


def scrub(text: str, secrets: list[str]) -> str:
    for value in secrets:
        text = text.replace(value, "[redacted]")
    return text


def _first_line(exc: BaseException) -> str:
    # Playwright errors carry a multi-line call log; the first line says what failed
    return (str(exc).strip().splitlines() or [type(exc).__name__])[0][:500]


class Replay:
    def __init__(
        self, browser: Browser, flow: BrowserFlow, base_url: str | None, args: dict[str, Any], creds: Credentials
    ) -> None:
        self.browser, self.flow, self.base_url, self.args, self.creds = browser, flow, base_url, args, creds
        self.hosts = hosts_for(base_url, flow.allowed_hosts)
        self.base_host = (urlparse(base_url or "").hostname or "").lower()
        self.result = FlowResult(ok=False)
        self.text_budget = MAX_TEXT_CHARS

    def _block(self, url: str) -> None:
        host = urlparse(url).hostname or url[:60]
        if host not in self.result.blocked_hosts:
            self.result.blocked_hosts.append(host)

    async def _guard(self, route: Route, request: Request) -> None:
        """The browser follows redirects without calling the route again, so the guard fetches each request
        itself without following redirects, checks the next hop, and hands the 3xx back to the browser (whose
        request for the new location comes through here again)."""
        url = request.url
        if not host_allowed(url, self.hosts):
            self._block(url)
            await route.abort("blockedbyclient")
            return
        headers = dict(request.headers)
        if self.creds.headers and (urlparse(url).hostname or "").lower() == self.base_host:
            headers |= self.creds.headers
        try:
            response = await route.fetch(headers=headers, max_redirects=0)
        except PlaywrightError:
            await route.abort("failed")
            return
        location = response.headers.get("location")
        if 300 <= response.status < 400 and location and not host_allowed(urljoin(url, location), self.hosts):
            self._block(urljoin(url, location))
            await route.abort("blockedbyclient")
            return
        await route.fulfill(response=response)

    def _url(self, raw: str) -> str:
        url = resolve_url(self.base_url, render(raw, self.args, in_url=True))
        if not host_allowed(url, self.hosts):
            raise StepFailed(f"{urlparse(url).hostname or url} is not an allowed host for this flow")
        return url

    def _value(self, step: BrowserStep) -> str:
        auth = self.creds.fields() if step.action == "fill" else None
        return render(step.value or "", self.args, auth)

    def _budget(self, value: Any) -> Any:
        size = len(str(value))
        if size > self.text_budget:
            raise StepFailed(f"extracted data exceeds {MAX_TEXT_CHARS} characters")
        self.text_budget -= size
        return value

    async def _shot(self, page: Page, name: str, full_page: bool = False) -> None:
        if len(self.result.screenshots) >= MAX_SCREENSHOTS:
            return
        png = await page.screenshot(type="png", full_page=full_page, mask=[page.locator(MASK)])
        self.result.screenshots.append({"name": name, "png": base64.b64encode(png).decode()})

    async def _step(self, page: Page, i: int, step: BrowserStep) -> None:
        timeout = step.timeout_ms or DEFAULT_STEP_TIMEOUT_MS
        loc: Locator | None = page.locator(step.selector) if step.selector else None
        one = loc.first if loc is not None else None
        match step.action:
            case "goto":
                await page.goto(self._url(step.url or ""), timeout=timeout, wait_until="load")
            case "click":
                assert one is not None
                await one.click(timeout=timeout)
            case "fill":
                assert one is not None
                await one.fill(self._value(step), timeout=timeout)
            case "select":
                assert one is not None
                await one.select_option(self._value(step), timeout=timeout)
            case "check":
                assert one is not None
                await one.check(timeout=timeout)
            case "uncheck":
                assert one is not None
                await one.uncheck(timeout=timeout)
            case "press":
                if one is not None:
                    await one.press(step.key or "", timeout=timeout)
                else:
                    await page.keyboard.press(step.key or "")
            case "wait_for" | "assert_visible":
                assert one is not None
                state = (step.state or "visible") if step.action == "wait_for" else "visible"
                await one.wait_for(state=state, timeout=timeout)
            case "wait_for_url":
                raw = render(step.url or "", self.args, in_url=True)
                await page.wait_for_url(
                    raw if raw.startswith("*") else resolve_url(self.base_url, raw), timeout=timeout
                )
            case "extract_text":
                assert loc is not None and one is not None and step.name
                if step.all:
                    self.result.data[step.name] = self._budget(await loc.all_inner_texts())
                else:
                    self.result.data[step.name] = self._budget((await one.inner_text(timeout=timeout)).strip())
            case "extract_table":
                assert one is not None and step.name
                await one.wait_for(state="attached", timeout=timeout)
                self.result.data[step.name] = self._budget(await one.evaluate(TABLE_JS, MAX_TABLE_ROWS))
            case "extract_attribute":
                assert loc is not None and one is not None and step.name and step.attribute
                if step.all:
                    values = await loc.evaluate_all("(els, a) => els.map((e) => e.getAttribute(a))", step.attribute)
                    self.result.data[step.name] = self._budget(values)
                else:
                    self.result.data[step.name] = self._budget(await one.get_attribute(step.attribute, timeout=timeout))
            case "assert_text":
                expected = self._value(step)
                text = await (
                    one.inner_text(timeout=timeout) if one else page.locator("body").inner_text(timeout=timeout)
                )
                if expected not in text:
                    raise StepFailed(f"expected text {expected!r} is not on the page")
            case "screenshot":
                await self._shot(page, step.name or f"step-{i}", bool(step.full_page))
        current = page.url
        if current not in {"about:blank", ""} and not host_allowed(current, self.hosts):
            raise StepFailed(f"the page left the allowed hosts ({urlparse(current).hostname})")

    async def run(self) -> FlowResult:
        context = await self.browser.new_context(
            accept_downloads=False, service_workers="block", ignore_https_errors=False
        )
        page: Page | None = None
        secrets = self.creds.secrets()
        try:
            await context.route("**/*", self._guard)
            page = await context.new_page()
            if self.flow.start_url:
                await page.goto(self._url(self.flow.start_url), wait_until="load", timeout=DEFAULT_STEP_TIMEOUT_MS)
            for i, step in enumerate(self.flow.steps, start=1):
                started = time.monotonic()
                try:
                    await self._step(page, i, step)
                except (PlaywrightError, StepFailed, FlowError) as exc:
                    message = scrub(_first_line(exc), secrets)
                    ms = int((time.monotonic() - started) * 1000)
                    self.result.steps.append(StepLog(i, step.action, False, ms, message))
                    if step.optional:
                        continue
                    self.result.error = f"step {i} ({step.action}) failed: {message}"
                    await self._failure_shot(page)
                    return self.result
                self.result.steps.append(StepLog(i, step.action, True, int((time.monotonic() - started) * 1000)))
            self.result.ok = True
            return self.result
        except (PlaywrightError, StepFailed, FlowError) as exc:
            self.result.error = scrub(_first_line(exc), secrets)
            if page is not None:
                await self._failure_shot(page)
            return self.result
        finally:
            if page is not None:
                with contextlib.suppress(PlaywrightError):
                    self.result.url, self.result.title = page.url, await page.title()
            await context.close()

    async def _failure_shot(self, page: Page) -> None:
        with contextlib.suppress(PlaywrightError):
            await self._shot(page, "failure")


async def replay(
    browser: Browser,
    flow: BrowserFlow,
    base_url: str | None,
    args: dict[str, Any],
    creds: Credentials,
    timeout_s: int | None = None,
) -> FlowResult:
    limit = timeout_s or flow.timeout_s or DEFAULT_FLOW_TIMEOUT_S
    job = Replay(browser, flow, base_url, args, creds)
    try:
        return await asyncio.wait_for(job.run(), timeout=limit)
    except TimeoutError:
        job.result.ok = False
        job.result.error = f"the flow did not finish within {limit} s"
        return job.result
