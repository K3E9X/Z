"""Browser operations (docs/05 §1, ADR 0016): the gateway resolves the credential and hands one reviewed flow to
the z-browser worker, which replays it in a fresh browser context limited to the connector's hosts.

The flow replayed is the one an admin reviewed: the operation's `source_ref` carries the digest of its steps,
and a flow whose steps no longer match it is refused. Screenshots come back as files; the caller stores them as
artifacts. Bearer and API-key credentials become request headers sent only to the connector's host; a basic
credential fills the `{{auth.username}}` and `{{auth.password}}` fields of a login form.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import httpx
from z_common.connectors.operations import flow_ref

from z_server.db.models import Connector, Operation
from z_server.gateway import http as http_invoker
from z_server.gateway.auth import AuthMaterial
from z_server.state import AppState

GRACE_S = 15  # the worker's own timeout fires first


@dataclass
class FlowRun:
    ok: bool
    body: Any
    latency_ms: int
    error: str | None
    files: list[tuple[str, bytes]]


def flow_name(op: Operation) -> str:
    return op.source_ref.removeprefix("flow:").split("@", 1)[0]


async def run(
    state: AppState, connector: Connector, op: Operation, args: dict[str, Any], auth: AuthMaterial, timeout_s: float
) -> FlowRun:
    settings = state.settings
    if not settings.browser_url:
        return FlowRun(False, None, 0, "browser connectors need the z-browser worker (Z_BROWSER_URL is not set)", [])
    name = flow_name(op)
    flow = (connector.manifest.get("flows") or {}).get(name)
    if flow is None or flow_ref(connector.manifest, name) != op.source_ref:
        return FlowRun(False, None, 0, f"flow {name} changed since its review; an admin must review it again", [])
    budget = int(min(max(float(flow.get("timeout_s") or timeout_s), 5), 300))
    body = {
        "flow": flow,
        "base_url": http_invoker.base_url(connector.base_url, connector.manifest),
        "args": args,
        "auth": {
            # a basic credential is a form login: the Authorization header would only reach the site's API
            "headers": {} if auth.login else auth.headers,
            "username": auth.login.get("username"),
            "password": auth.login.get("password"),
        },
        "timeout_s": budget,
    }
    started = time.monotonic()
    try:
        resp = await state.http.post(
            f"{settings.browser_url}/v1/run",
            json=body,
            headers={"Authorization": f"Bearer {settings.browser_token}"},
            timeout=budget + GRACE_S,
        )
    except httpx.HTTPError as exc:
        return FlowRun(False, None, _ms(started), f"the browser worker is unreachable ({type(exc).__name__})", [])
    if resp.status_code != 200:
        return FlowRun(False, None, _ms(started), f"the browser worker answered {resp.status_code}", [])
    result = resp.json()
    files = [
        (f"{op.local_id}-{shot.get('name') or i}.png", base64.b64decode(shot["png"]))
        for i, shot in enumerate(result.get("screenshots") or [], start=1)
        if shot.get("png")
    ]
    failed = [s for s in result.get("steps") or [] if not s.get("ok")]
    out: dict[str, Any] = {"url": result.get("url"), "title": result.get("title"), "data": result.get("data") or {}}
    if failed:
        out["failed_steps"] = [
            {"step": s.get("index"), "action": s.get("action"), "error": s.get("error")} for s in failed
        ]
    if result.get("blocked_hosts"):
        out["blocked_hosts"] = result["blocked_hosts"]
    return FlowRun(bool(result.get("ok")), out, _ms(started), result.get("error"), files)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
