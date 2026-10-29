"""Replays against a small local site with a real Chromium (skipped when no browser is installed)."""

from __future__ import annotations

import base64
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from z_common.models.manifests import BrowserFlow

pw = pytest.importorskip("playwright.async_api")

from z_browser.app import create_app  # noqa: E402
from z_browser.flows import Credentials, replay  # noqa: E402

PASSWORD = "s3cret-pass-word"
HITS: list[str] = []  # Host header of every request the site served
RULES = [("allow-dns", "udp/53", "on"), ("deny-telnet", "tcp/23", "on"), ("legacy-ftp", "tcp/21", "off")]


def site() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def record(request: Request, call_next: Any) -> Any:
        HITS.append(request.headers.get("host", "").split(":")[0])
        return await call_next(request)

    @app.get("/login", response_class=HTMLResponse)
    async def login() -> str:
        return """<html><title>Firewall</title><body><form method="post" action="/session">
        <input name="username" id="user"><input type="password" name="password" id="pass">
        <button id="go" type="submit">Sign in</button></form></body></html>"""

    @app.post("/session")
    async def session(username: str = Form(...), password: str = Form(...)) -> Any:
        if username != "auditor" or password != PASSWORD:
            return HTMLResponse("<p id='err'>Wrong password</p>", status_code=401)
        out = RedirectResponse("/rules", status_code=303)
        out.set_cookie("sid", "ok")
        return out

    @app.get("/rules", response_class=HTMLResponse)
    async def rules(request: Request, q: str = "") -> Any:
        if request.cookies.get("sid") != "ok":
            return RedirectResponse("/login", status_code=303)
        rows = "".join(f"<tr><td>{n}</td><td>{p}</td><td>{s}</td></tr>" for n, p, s in RULES if q.lower() in n.lower())
        return f"""<html><title>Rules</title><body><h1 id="h">Rules matching '{q}'</h1>
        <table id="rules"><tr><th>Name</th><th>Port</th><th>State</th></tr>{rows}</table>
        <a class="doc" href="/doc/1">one</a><a class="doc" href="/doc/2">two</a></body></html>"""

    @app.get("/leak", response_class=HTMLResponse)
    async def leak(request: Request) -> str:
        port = request.url.port
        return f"<html><body><p id='p'>page</p><img src='http://localhost:{port}/pixel'></body></html>"

    @app.get("/out")
    async def out(request: Request) -> Any:
        return RedirectResponse(f"http://localhost:{request.url.port}/rules", status_code=302)

    @app.get("/headers", response_class=HTMLResponse)
    async def headers(request: Request) -> str:
        return f"<p id='auth'>{'with-token' if request.headers.get('x-api-key') else 'no-token'}</p>"

    @app.get("/pixel")
    async def pixel() -> HTMLResponse:
        return HTMLResponse("")

    return app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(site(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


@pytest.fixture
async def browser() -> AsyncIterator[Any]:
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except pw.Error as exc:  # no browser build installed here
            pytest.skip(f"chromium unavailable: {str(exc).splitlines()[0]}")
        yield b
        await b.close()


LOGIN = [
    {"action": "fill", "selector": "#user", "value": "{{auth.username}}"},
    {"action": "fill", "selector": "#pass", "value": "{{auth.password}}"},
    {"action": "click", "selector": "#go"},
    {"action": "wait_for_url", "url": "**/rules"},
]


async def test_login_filter_and_extract(browser: Any, base_url: str) -> None:
    flow = BrowserFlow.model_validate(
        {
            "start_url": "/login",
            "steps": [
                *LOGIN,
                {"action": "goto", "url": "/rules?q={{args.name}}"},
                {"action": "assert_text", "selector": "#h", "value": "{{args.name}}"},
                {"action": "extract_table", "selector": "#rules", "name": "rules"},
                {"action": "extract_attribute", "selector": "a.doc", "attribute": "href", "all": True, "name": "docs"},
                {"action": "screenshot", "name": "rules"},
            ],
        }
    )
    creds = Credentials(username="auditor", password=PASSWORD)
    result = await replay(browser, flow, base_url, {"name": "deny"}, creds)
    assert result.ok, result.error
    assert result.data["rules"] == [{"Name": "deny-telnet", "Port": "tcp/23", "State": "on"}]
    assert result.data["docs"] == ["/doc/1", "/doc/2"]
    assert result.title == "Rules" and result.url and result.url.endswith("/rules?q=deny")
    assert [s["name"] for s in result.screenshots] == ["rules"]
    assert base64.b64decode(result.screenshots[0]["png"]).startswith(b"\x89PNG")
    assert all(s.ok for s in result.steps)


async def test_failure_is_scrubbed_and_screenshotted(browser: Any, base_url: str) -> None:
    flow = BrowserFlow.model_validate(
        {
            "start_url": "/login",
            "steps": [
                {"action": "fill", "selector": "#pass", "value": "{{auth.password}}"},
                {"action": "click", "selector": "#missing", "timeout_ms": 300},
            ],
        }
    )
    result = await replay(browser, flow, base_url, {}, Credentials(username="auditor", password=PASSWORD))
    assert not result.ok and result.error and "step 2 (click)" in result.error
    assert PASSWORD not in str(result.__dict__)
    assert result.screenshots and result.screenshots[0]["name"] == "failure"


async def test_optional_step_does_not_fail_the_flow(browser: Any, base_url: str) -> None:
    flow = BrowserFlow.model_validate(
        {
            "start_url": "/leak",
            "steps": [
                {"action": "click", "selector": "#cookie-banner", "optional": True, "timeout_ms": 200},
                {"action": "extract_text", "selector": "#p", "name": "p"},
            ],
        }
    )
    HITS.clear()
    result = await replay(browser, flow, base_url, {}, Credentials())
    assert result.ok and result.data == {"p": "page"}
    assert "localhost" not in HITS
    assert result.steps[0].ok is False
    assert result.blocked_hosts == ["localhost"]  # the image on another host was never fetched


async def test_navigation_off_the_allowlist_is_refused(browser: Any, base_url: str) -> None:
    HITS.clear()
    redirect = BrowserFlow.model_validate({"steps": [{"action": "goto", "url": "/out"}]})
    result = await replay(browser, redirect, base_url, {}, Credentials())
    assert not result.ok and "localhost" in result.blocked_hosts
    assert HITS == ["127.0.0.1"]  # the redirect target on another host was never requested
    direct = BrowserFlow.model_validate({"steps": [{"action": "goto", "url": "https://example.org/"}]})
    result = await replay(browser, direct, base_url, {}, Credentials())
    assert not result.ok and result.error and "not an allowed host" in result.error


async def test_credential_headers_only_go_to_the_connector_host(browser: Any, base_url: str) -> None:
    flow = BrowserFlow.model_validate(
        {
            "start_url": "/headers",
            "allowed_hosts": ["localhost"],
            "steps": [
                {"action": "extract_text", "selector": "#auth", "name": "base"},
                {"action": "goto", "url": base_url.replace("127.0.0.1", "localhost") + "/headers"},
                {"action": "extract_text", "selector": "#auth", "name": "other"},
            ],
        }
    )
    result = await replay(browser, flow, base_url, {}, Credentials(headers={"X-API-Key": "k-123456789"}))
    assert result.ok, result.error
    assert result.data == {"base": "with-token", "other": "no-token"}


async def test_timeout(browser: Any, base_url: str) -> None:
    flow = BrowserFlow.model_validate(
        {"start_url": "/leak", "steps": [{"action": "wait_for", "selector": "#never", "timeout_ms": 20_000}]}
    )
    result = await replay(browser, flow, base_url, {}, Credentials(), timeout_s=5)
    assert not result.ok and result.error == "the flow did not finish within 5 s"


async def test_api_requires_the_token(base_url: str) -> None:
    app = create_app("t" * 32)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://z-browser") as client:
        assert (await client.get("/healthz")).status_code == 200
        body = {"flow": {"steps": [{"action": "screenshot"}]}, "base_url": base_url}
        assert (await client.post("/v1/run", json=body)).status_code == 401
        bad = {"flow": {"steps": [{"action": "click"}]}}
        resp = await client.post("/v1/run", json=bad, headers={"Authorization": "Bearer " + "t" * 32})
        assert resp.status_code == 422  # a click needs a selector
    with pytest.raises(RuntimeError):
        create_app("short")


async def center(rec: Any, selector: str) -> tuple[float, float]:
    box = await rec.page.locator(selector).first.bounding_box()
    assert box is not None
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


async def test_record_then_replay(browser: Any, base_url: str) -> None:
    from z_browser.record import Recorder, RecordError

    recorder = Recorder()
    creds = Credentials(username="auditor", password=PASSWORD)
    rec = await recorder.start(browser, base_url, None, "/login", creds)
    for field, sel in (("username", "#user"), ("password", "#pass")):
        x, y = await center(rec, sel)
        await rec.act({"kind": "click", "x": x, "y": y})
        await rec.act({"kind": "credential", "field": field})
    x, y = await center(rec, "#go")
    await rec.act({"kind": "click", "x": x, "y": y})
    await rec.act({"kind": "goto", "url": "/rules?q=deny"})
    x, y = await center(rec, "#rules td")
    await rec.act({"kind": "extract", "what": "table", "x": x, "y": y, "name": "rules"})
    with pytest.raises(RecordError, match="not an allowed host"):
        await rec.act({"kind": "goto", "url": "https://example.org/"})
    state = await rec.state()
    assert state["url"] == "rules?q=deny" or state["url"].endswith("/rules?q=deny")
    assert base64.b64decode(state["screen_jpeg"])[:2] == b"\xff\xd8"  # a JPEG
    steps = rec.flow()["steps"]
    assert [s["action"] for s in steps] == ["click", "fill", "click", "fill", "click", "goto", "extract_table"]
    assert steps[1]["value"] == "{{auth.username}}" and steps[3]["value"] == "{{auth.password}}"
    assert PASSWORD not in str(steps)  # the credential is typed, never recorded
    assert steps[1]["selector"] == "#user" and steps[-1]["selector"] == "#rules"
    flow = BrowserFlow.model_validate(rec.flow())  # the draft passes the same checks as a reviewed flow
    await recorder.close(rec.id)
    replayed = await replay(browser, flow, base_url, {}, creds)
    assert replayed.ok, replayed.error
    assert replayed.data["rules"] == [{"Name": "deny-telnet", "Port": "tcp/23", "State": "on"}]


async def test_typed_arguments_become_placeholders(browser: Any, base_url: str) -> None:
    from z_browser.record import Recorder

    recorder = Recorder()
    rec = await recorder.start(browser, base_url, None, "/login", Credentials())
    x, y = await center(rec, "#user")
    await rec.act({"kind": "click", "x": x, "y": y})
    await rec.act({"kind": "type", "text": "auditor", "arg": "who"})
    await rec.act({"kind": "type", "text": "literal"})
    await rec.act({"kind": "undo"})
    assert rec.flow()["steps"][-1] == {"action": "fill", "selector": "#user", "value": "{{args.who}}"}
    await recorder.close(rec.id)
