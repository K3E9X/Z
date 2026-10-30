"""HTTP hardening (docs/12 §5): security headers, request ids, body limits, SSRF guard for admin-entered URLs."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from z_server.errors import Problem

if TYPE_CHECKING:
    from z_server.state import AppState

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
MAX_BODY = 10 * 1024 * 1024  # artifacts have their own limit on the internal route

# k3s defaults (10.42/16 pods, 10.43/16 services) and kubeadm's default service range.
DEFAULT_CLUSTER_CIDRS = "10.42.0.0/16,10.43.0.0/16,10.96.0.0/12"
METADATA = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
    ipaddress.ip_address("100.100.100.200"),
}


class BodyLimit:
    """MAX_BODY for bodies without a Content-Length too (chunked): SecurityHeaders only sees the header. The body is
    read up to the limit and replayed to the app, or answered with 413 (the internal runner routes have their own)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = dict(scope.get("headers") or []) if scope["type"] == "http" else {}
        if (
            scope["type"] != "http"
            or b"content-length" in headers
            or str(scope.get("path", "")).startswith("/api/v1/internal/")
            or scope.get("method") in {"GET", "HEAD", "OPTIONS", "DELETE"}
        ):
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size, more = 0, True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_BODY:
                response = Response(
                    '{"type":"urn:z:problem:validation","title":"request body too large","status":413}',
                    status_code=413,
                    media_type="application/problem+json",
                )
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            more = bool(message.get("more_body"))
        replay = [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]

        async def replayed() -> Message:
            return replay.pop() if replay else await receive()

        await self.app(scope, replayed, send)


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        length = request.headers.get("content-length")
        if (
            length
            and length.isdigit()
            and int(length) > MAX_BODY
            and not request.url.path.startswith("/api/v1/internal/")
        ):
            from z_server.errors import problem_response

            return problem_response(Problem("validation", "request body too large", status=413), request)
        response = await call_next(request)
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers["X-Request-Id"] = request.state.request_id
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def _networks(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    return [ipaddress.ip_network(c.strip(), strict=False) for c in raw.split(",") if c.strip()]


def blocked_reason(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, cluster: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> str | None:
    if ip in METADATA:
        return "cloud metadata address"
    if ip.is_loopback:
        return "loopback address"
    if ip.is_link_local:
        return "link-local address"
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "non-routable address"
    if any(ip in net for net in cluster):
        return "cluster-internal address"
    return None


async def check_outbound_url(url: str, state: AppState | None = None) -> None:
    """Refuse URLs that resolve to loopback, link-local, metadata or cluster-internal ranges unless the host
    is in Z_OUTBOUND_ALLOW (comma-separated host names or CIDRs)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http", "tls", "postgresql", "postgres"} or not parsed.hostname:
        raise Problem("validation", "URL must be http(s) (or postgresql:// for a database connector) with a host")
    allow = [a.strip() for a in os.environ.get("Z_OUTBOUND_ALLOW", "").split(",") if a.strip()]
    host = parsed.hostname
    if host in allow:
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise Problem("validation", f"cannot resolve {host}") from exc
    allowed_nets = _networks(",".join(a for a in allow if "/" in a))
    cluster = _networks(os.environ.get("Z_CLUSTER_CIDRS", DEFAULT_CLUSTER_CIDRS))
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if any(ip in net for net in allowed_nets):
            continue
        reason = blocked_reason(ip, cluster)
        if reason:
            raise Problem("validation", f"{host} resolves to a {reason}; add it to Z_OUTBOUND_ALLOW if intended")
