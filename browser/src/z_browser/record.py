"""Record a browser flow by demonstration (docs/05 §6 "From the browser", ADR 0016).

An admin drives one live page from the web app: each input (click at a point, typed text, a credential, a key,
a URL, an extraction or an assertion) is performed and recorded as a flow step with a selector that matches
exactly one element. The page runs behind the same guard as a replay (allowlisted hosts only). Credentials are
never typed by the admin: "username"/"password" inputs make the worker type the connector's credential and
record `{{auth.username}}`/`{{auth.password}}`. Text typed as an argument is recorded as `{{args.<name>}}`.
The result is a draft flow, reviewed and saved like any connector change.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, ViewportSize
from playwright.async_api import Error as PlaywrightError
from z_common.connectors.browser import host_allowed, hosts_for, resolve_url

from z_browser.flows import MASK, Credentials, Guard, scrub

VIEWPORT: ViewportSize = {"width": 1280, "height": 800}
IDLE_TTL_S = 600
MAX_STEPS = 200
ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
OUTPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")

# Candidate selectors for an element, most robust first; Python keeps the first one that matches exactly once.
CANDIDATES_JS = """(el) => {
  const esc = (v) => CSS.escape(v);
  const q = (v) => JSON.stringify(v);
  const out = [];
  const tag = el.tagName.toLowerCase();
  if (el.id && !/\\d{4,}/.test(el.id)) out.push('#' + esc(el.id));
  for (const a of ['data-testid', 'data-test', 'data-qa', 'name', 'aria-label', 'placeholder', 'title']) {
    const v = el.getAttribute(a);
    if (v) out.push(tag + '[' + a + '=' + q(v) + ']');
  }
  const text = (el.innerText || el.value || '').trim().split('\\n')[0].slice(0, 60);
  if (text && ['button', 'a', 'label', 'summary', 'th', 'td', 'li', 'span', 'h1', 'h2', 'h3'].includes(tag))
    out.push(tag + ':has-text(' + q(text) + ')');
  const path = [];
  let node = el;
  while (node && node.nodeType === 1 && path.length < 6) {
    let part = node.tagName.toLowerCase();
    if (node.id && !/\\d{4,}/.test(node.id)) { path.unshift('#' + esc(node.id)); break; }
    const parent = node.parentElement;
    if (parent) {
      const same = [...parent.children].filter((c) => c.tagName === node.tagName);
      if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
    }
    path.unshift(part);
    node = parent;
  }
  out.push(path.join(' > '));
  return out;
}"""

# The element a person means when clicking at a point: the closest actionable ancestor.
TARGET_JS = """([x, y, kind]) => {
  let el = document.elementFromPoint(x, y);
  if (!el) return null;
  const pick = kind === 'table' ? 'table'
    : 'button, a, input, select, textarea, label, summary, [role=button], [role=link], [role=tab], [onclick]';
  const found = el.closest(pick);
  if (found || kind === 'table') el = found;
  if (!el || el === document.body || el === document.documentElement) return null;
  window.__zTarget = el;
  return el.tagName.toLowerCase();
}"""

InputKind = Literal["click", "type", "credential", "press", "goto", "extract", "assert", "screenshot", "undo"]


class RecordError(ValueError):
    pass


@dataclass
class Recording:
    id: str
    context: BrowserContext
    page: Page
    base_url: str | None
    hosts: list[str]
    creds: Credentials
    start_url: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def relative(self, url: str) -> str:
        base = (self.base_url or "").rstrip("/") + "/"
        return url[len(base) :] if self.base_url and url.startswith(base) else url

    async def selector(self, handle_js: str = "window.__zTarget") -> str:
        candidates: list[str] = await self.page.evaluate(f"(js) => ({CANDIDATES_JS})({handle_js})", None)
        for candidate in candidates:
            try:
                if await self.page.locator(candidate).count() == 1:
                    return candidate
            except PlaywrightError:
                continue
        raise RecordError("no selector matches only this element: pick a more specific element")

    async def target(self, x: float, y: float, kind: str = "") -> str:
        if not (0 <= x <= VIEWPORT["width"] and 0 <= y <= VIEWPORT["height"]):
            raise RecordError("the point is outside the page")
        tag = await self.page.evaluate(TARGET_JS, [x, y, kind])
        if tag is None:
            raise RecordError("no table at this point" if kind == "table" else "nothing to act on at this point")
        return await self.selector()

    async def focused(self) -> str:
        tag = await self.page.evaluate(
            "() => { window.__zTarget = document.activeElement; return document.activeElement?.tagName }"
        )
        if not tag or tag.lower() in {"body", "html"}:
            raise RecordError("click the field to type into first")
        return await self.selector()

    async def settle(self) -> None:
        with contextlib.suppress(PlaywrightError):
            await self.page.wait_for_load_state("load", timeout=10_000)

    def add(self, step: dict[str, Any]) -> None:
        if len(self.steps) >= MAX_STEPS:
            raise RecordError(f"a flow has at most {MAX_STEPS} steps")
        self.steps.append(step)

    async def act(self, body: dict[str, Any]) -> None:
        kind: InputKind = body["kind"]
        page = self.page
        if kind == "click":
            selector = await self.target(float(body["x"]), float(body["y"]))
            await page.locator(selector).first.click(timeout=10_000)
            self.add({"action": "click", "selector": selector})
            await self.settle()
        elif kind == "type":
            selector = await self.focused()
            text = str(body.get("text") or "")
            arg = body.get("arg")
            if arg is not None and not ARG_NAME.match(str(arg)):
                raise RecordError("an argument name is letters, digits and _")
            await page.locator(selector).first.fill(text, timeout=10_000)
            self.add({"action": "fill", "selector": selector, "value": f"{{{{args.{arg}}}}}" if arg else text})
        elif kind == "credential":
            name = body.get("field")
            value = self.creds.fields().get(str(name)) if name in {"username", "password"} else None
            if value is None:
                raise RecordError(f"the connector credential has no {name}")
            selector = await self.focused()
            await page.locator(selector).first.fill(value, timeout=10_000)
            self.add({"action": "fill", "selector": selector, "value": f"{{{{auth.{name}}}}}"})
        elif kind == "press":
            key = str(body.get("key") or "")
            if key not in {"Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "Space"}:
                raise RecordError("keys: Enter, Tab, Escape, ArrowDown, ArrowUp, Space")
            await page.keyboard.press(key)
            self.add({"action": "press", "key": key})
            await self.settle()
        elif kind == "goto":
            url = resolve_url(self.base_url, str(body.get("url") or ""))
            if not host_allowed(url, self.hosts):
                raise RecordError(f"{urlparse(url).hostname or url} is not an allowed host")
            await page.goto(url, wait_until="load", timeout=15_000)
            self.add({"action": "goto", "url": self.relative(url)})
        elif kind == "extract":
            name = str(body.get("name") or "")
            if not OUTPUT_NAME.match(name):
                raise RecordError("name the extracted value (letters, digits and _)")
            what = str(body.get("what") or "text")
            if what == "table":
                selector = await self.target(float(body["x"]), float(body["y"]), "table")
                self.add({"action": "extract_table", "selector": selector, "name": name})
            elif what == "link":
                selector = await self.target(float(body["x"]), float(body["y"]))
                self.add({"action": "extract_attribute", "selector": selector, "attribute": "href", "name": name})
            else:
                selector = await self.target(float(body["x"]), float(body["y"]))
                self.add({"action": "extract_text", "selector": selector, "name": name})
        elif kind == "assert":
            selector = await self.target(float(body["x"]), float(body["y"]))
            self.add({"action": "assert_visible", "selector": selector})
        elif kind == "screenshot":
            self.add({"action": "screenshot", "name": str(body.get("name") or f"step-{len(self.steps) + 1}")})
        elif kind == "undo":
            if self.steps:
                self.steps.pop()  # the page stays where it is; only the recorded step goes
        else:  # pragma: no cover - the API model restricts kinds
            raise RecordError(f"unknown input {kind}")

    async def state(self, with_screen: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "url": self.relative(self.page.url),
            "title": await self.page.title(),
            "steps": list(self.steps),
            "blocked_hosts": list(self.blocked),
            "viewport": VIEWPORT,
        }
        if with_screen:
            png = await self.page.screenshot(type="jpeg", quality=70, mask=[self.page.locator(MASK)])
            out["screen_jpeg"] = base64.b64encode(png).decode()
        return out

    def flow(self) -> dict[str, Any]:
        return {"start_url": self.start_url, "steps": list(self.steps)}


class Recorder:
    """At most `limit` live recordings per worker; an idle one closes after 10 minutes."""

    def __init__(self, limit: int = 2) -> None:
        self.sessions: dict[str, Recording] = {}
        self.limit = limit

    async def start(
        self,
        browser: Browser,
        base_url: str | None,
        allowed_hosts: list[str] | None,
        start_url: str,
        creds: Credentials,
    ) -> Recording:
        await self.sweep()
        if len(self.sessions) >= self.limit:
            raise RecordError("too many recordings at once on this worker: finish or cancel one")
        hosts = hosts_for(base_url, allowed_hosts)
        url = resolve_url(base_url, start_url)
        if not host_allowed(url, hosts):
            raise RecordError(f"{urlparse(url).hostname or url} is not an allowed host")
        context = await browser.new_context(
            accept_downloads=False, service_workers="block", viewport=VIEWPORT, ignore_https_errors=False
        )
        rec = Recording(secrets.token_urlsafe(16), context, await context.new_page(), base_url, hosts, creds, start_url)
        await context.route("**/*", Guard(hosts, base_url, creds, rec.blocked))
        try:
            await rec.page.goto(url, wait_until="load", timeout=15_000)
        except PlaywrightError as exc:
            await context.close()
            raise RecordError(scrub(str(exc).splitlines()[0][:300], creds.secrets())) from exc
        self.sessions[rec.id] = rec
        return rec

    def get(self, rec_id: str) -> Recording:
        rec = self.sessions.get(rec_id)
        if rec is None:
            raise KeyError(rec_id)
        rec.touched = time.monotonic()
        return rec

    async def close(self, rec_id: str) -> Recording | None:
        rec = self.sessions.pop(rec_id, None)
        if rec is not None:
            await rec.context.close()
        return rec

    async def sweep(self) -> None:
        now = time.monotonic()
        for rec_id in [k for k, r in self.sessions.items() if now - r.touched > IDLE_TTL_S]:
            await self.close(rec_id)
