"""HTTP invoker for `openapi` and `rest` connectors (docs/05 §2 steps 4 and 7).

`source_ref` is `METHOD /path/{param}`. Arguments map to the request this way: `{param}` placeholders from the
argument of that name; an argument named `body` is the JSON body (or the raw text body when its schema has
`x-content-type`, from a text/* or application/octet-stream request body in the spec); properties marked `x-in: query|path|body|header`
in the input schema go there; remaining arguments go to the query string for GET/HEAD/DELETE and into the
JSON body otherwise. Idempotent reads are retried on 429/502/503/504 and network errors (Retry-After honoured,
capped). Responses larger than MAX_RESPONSE_BYTES are refused rather than buffered.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from z_common.config import substitute

PARAM = re.compile(r"\{([A-Za-z0-9_.-]+)\}")
IDEMPOTENT = {"GET", "HEAD"}
RETRY_STATUS = {429, 502, 503, 504}
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_RETRY_AFTER_S = 10.0
MAX_RAW_BODY_BYTES = 4 * 1024 * 1024  # raw (file) bodies an agent may send; larger files need a resumable upload


class InvokeError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class HttpResult:
    status: int
    body: Any
    content_type: str
    latency_ms: int
    attempts: int


@dataclass
class PreparedRequest:
    method: str
    url: str
    params: dict[str, Any]
    json: Any
    headers: dict[str, str]
    content: bytes | None = None  # a raw body, sent instead of `json`


def base_url(connector_base: str | None, manifest: dict[str, Any], env: dict[str, str] | None = None) -> str:
    raw = connector_base or manifest.get("base_url") or ""
    value = substitute(raw, env if env is not None else os.environ)
    if not isinstance(value, str) or not value or "${" in value:
        raise InvokeError("connector base_url is not configured")
    return value.rstrip("/")


def prepare(base: str, source_ref: str, input_schema: dict[str, Any], args: dict[str, Any]) -> PreparedRequest:
    method, _, path = source_ref.partition(" ")
    method = method.upper()
    if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"} or not path.startswith("/"):
        raise InvokeError(f"operation source {source_ref!r} is not an HTTP operation")
    remaining = dict(args)
    props: dict[str, Any] = (input_schema or {}).get("properties") or {}

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in remaining:
            raise InvokeError(f"missing path parameter {name}")
        value = str(remaining.pop(name))
        if value in {"", ".", ".."}:  # a dot segment would move the call to another endpoint of the tool
            raise InvokeError(f"path parameter {name} cannot be {value!r}")
        return quote(value, safe="")

    path = PARAM.sub(fill, path)
    params: dict[str, Any] = {}
    headers: dict[str, str] = {}
    body: Any = remaining.pop("body", None)
    for name in list(remaining):
        where = (props.get(name) or {}).get("x-in")
        if where == "query":
            params[name] = remaining.pop(name)
        elif where == "header":
            headers[name] = str(remaining.pop(name))
        elif where == "body":
            body = {**(body or {}), name: remaining.pop(name)}
    if remaining:
        if method in {"GET", "HEAD", "DELETE"}:
            params.update(remaining)
        else:
            body = {**(body if isinstance(body, dict) else {}), **remaining}
    params = {k: (json.dumps(v) if isinstance(v, dict | list) else v) for k, v in params.items() if v is not None}
    raw_type = (props.get("body") or {}).get("x-content-type")
    if raw_type and method not in IDEMPOTENT:
        if not isinstance(body, str):
            raise InvokeError("this operation takes its body as text")
        if len(body.encode()) > MAX_RAW_BODY_BYTES:
            raise InvokeError(f"the body is larger than {MAX_RAW_BODY_BYTES // (1024 * 1024)} MB")
        return PreparedRequest(
            method, base + path, params, None, {**headers, "Content-Type": str(raw_type)}, body.encode()
        )
    return PreparedRequest(method, base + path, params, body, headers)


def outcome(manifest: dict[str, Any], status: int, body: Any) -> tuple[bool, str | None]:
    """(ok, error) of an HTTP answer: a 2xx is ok unless the manifest's `error_body` says the body reports an error
    (Slack answers 200 with {"ok": false, "error": "channel_not_found"})."""
    if not 200 <= status < 300:
        return False, f"HTTP {status}"
    rule = manifest.get("error_body") or {}
    if not rule or not isinstance(body, dict):
        return True, None

    def field(path: str) -> Any:
        value: Any = body
        for part in path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        return value

    if field(str(rule["path"])) != rule.get("equals"):
        return True, None
    reason = field(str(rule["message"])) if rule.get("message") else None
    return False, str(reason or f"the tool reported an error ({rule['path']} = {rule.get('equals')!r})")[:500]


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    raw = resp.headers.get("retry-after", "")
    try:
        return min(float(raw), MAX_RETRY_AFTER_S)
    except ValueError:
        return min(0.5 * float(2**attempt), MAX_RETRY_AFTER_S)


async def send(
    http: httpx.AsyncClient,
    request: PreparedRequest,
    auth_headers: dict[str, str],
    *,
    timeout_s: float = 30.0,
    retries: int = 2,
) -> HttpResult:
    headers = {"Accept": "application/json", "User-Agent": "z-gateway", **request.headers, **auth_headers}
    idempotent = request.method in IDEMPOTENT
    attempts = 0
    started = time.monotonic()
    while True:
        attempts += 1
        try:
            async with http.stream(
                request.method,
                request.url,
                params=request.params or None,
                json=request.json if request.method not in IDEMPOTENT and request.content is None else None,
                content=request.content if request.method not in IDEMPOTENT else None,
                headers=headers,
                timeout=timeout_s,
                follow_redirects=False,
            ) as resp:
                if resp.status_code in RETRY_STATUS and idempotent and attempts <= retries:
                    await asyncio.sleep(_retry_after(resp, attempts))
                    continue
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise InvokeError(f"response larger than {MAX_RESPONSE_BYTES} bytes", resp.status_code)
                    chunks.append(chunk)
                raw = b"".join(chunks)
                content_type = resp.headers.get("content-type", "")
                status = resp.status_code
        except httpx.TimeoutException as exc:
            if idempotent and attempts <= retries:
                continue
            raise InvokeError(f"timeout after {timeout_s:.0f} s", retryable=True) from exc
        except httpx.HTTPError as exc:
            if idempotent and attempts <= retries:
                await asyncio.sleep(0.5 * attempts)
                continue
            raise InvokeError(f"{type(exc).__name__}: tool unreachable", retryable=True) from exc
        body: Any = raw.decode("utf-8", errors="replace")
        if "json" in content_type and raw:
            with contextlib.suppress(ValueError):
                body = json.loads(raw)
        elif not raw:
            body = None
        return HttpResult(status, body, content_type, int((time.monotonic() - started) * 1000), attempts)
