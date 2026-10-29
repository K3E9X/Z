"""HTTP client for the Z API (`/api/v1`, contracts/openapi.yaml): bearer auth, problem+json errors, pagination.

Errors map to exit codes (docs/10 §3):
- 401 unauthenticated / step-up-required, 403 forbidden -> 4 (auth)
- 422 validation -> 2 (usage), 402 budget-exceeded -> 5, everything else -> 1
- a 404/405 on the route itself (not on a resource) means the server does not serve that endpoint yet:
  the CLI says so instead of pretending.
"""

from __future__ import annotations

import json as jsonlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import httpx
from pydantic import ValidationError
from z_common.models.common import Problem

from z_cli import __version__
from z_cli.errors import (
    ApiError,
    AuthRequired,
    CliError,
    ExitCode,
    Forbidden,
    NotAvailable,
    NotSignedIn,
    StepUpRequired,
)

API_PREFIX = "/api/v1"
USER_AGENT = f"z-cli/{__version__}"
ROUTE_NOT_FOUND = {"Not Found", "Method Not Allowed"}  # Starlette's text when no route matches
Params = dict[str, Any]


def _clean(params: Params | None) -> Params | None:
    if params is None:
        return None
    return {k: v for k, v in params.items() if v is not None and v != ""}


def _problem(response: httpx.Response) -> Problem | None:
    if not response.headers.get("content-type", "").startswith(("application/problem+json", "application/json")):
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or "type" not in body or "title" not in body:
        return None
    try:
        return Problem.model_validate({k: v for k, v in body.items() if k in Problem.model_fields})
    except ValidationError:
        return None


def is_route_missing(response: httpx.Response) -> bool:
    """True when no route matched (the endpoint is not served), as opposed to a missing resource."""
    if response.status_code == 405:
        return True
    if response.status_code != 404:
        return False
    problem = _problem(response)
    return problem is None or (problem.detail or "") in ROUTE_NOT_FOUND


def error_for(response: httpx.Response, feature: str, *, signed_in: bool, server: str) -> CliError:
    status = response.status_code
    if is_route_missing(response):
        return NotAvailable(feature)
    problem = _problem(response)
    if problem is None:
        text = response.text.strip().splitlines()[0][:200] if response.text.strip() else response.reason_phrase
        problem = Problem(type="urn:z:problem:http", title=f"HTTP {status}", status=status, detail=text or None)
    kind = problem.type.removeprefix("urn:z:problem:")
    if kind == "step-up-required":
        return StepUpRequired(
            status,
            problem,
            exit_code=ExitCode.AUTH,
            message="This action needs a fresh sign-in (step-up re-authentication).",
            hint=(
                "Z offers step-up to browser sessions only: sign in again in the web app "
                f"({server}) and do it there, or ask an admin. CLI step-up is not available on this server yet."
            ),
        )
    if status == 401:
        if not signed_in:
            return NotSignedIn(server)
        return AuthRequired(
            status,
            problem,
            exit_code=ExitCode.AUTH,
            message="Your Z session expired or was revoked.",
            hint="Run `z login` to sign in again.",
        )
    if kind == "forbidden":
        detail = problem.detail or "permission denied"
        if problem.permission and problem.permission not in detail:
            detail += f" (needs {problem.permission})"
        return Forbidden(
            status,
            problem,
            exit_code=ExitCode.AUTH,
            message=f"Not allowed: {detail}",
            hint="Ask an admin for the role that grants it (`z whoami` shows yours).",
        )
    if kind == "separation-of-duties":
        return ApiError(status, problem, message=f"Separation of duties: {problem.detail or 'refused'}")
    if kind == "validation":
        lines = [problem.detail or "invalid request"]
        lines += [f"  {e.path or '(body)'}: {e.message}" for e in problem.errors or []]
        return ApiError(status, problem, exit_code=ExitCode.USAGE, message="\n".join(lines))
    if kind == "budget-exceeded":
        return ApiError(status, problem, exit_code=ExitCode.BUDGET)
    if kind == "rate-limited":
        retry = response.headers.get("retry-after")
        return ApiError(status, problem, hint=f"Retry in {retry}s." if retry else "Wait a moment and retry.")
    if kind == "not-found":
        return ApiError(status, problem, message=f"Not found: {problem.detail or feature}")
    return ApiError(status, problem)


def _network_error(exc: httpx.HTTPError, server: str) -> CliError:
    if isinstance(exc, httpx.TimeoutException):
        return CliError(f"Z at {server} did not answer in time.", hint="Check the server, or retry.")
    return CliError(
        f"Cannot reach Z at {server}: {exc.__class__.__name__}: {exc}",
        hint="Check the URL (`z config get url`), your network or VPN.",
    )


def _decode(response: httpx.Response) -> Any:
    if response.status_code == 204 or not response.content:
        return None
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return response.text


class _Base:
    def __init__(self, url: str, token: str | None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def feature(self, method: str, path: str) -> str:
        return f"{method.upper()} {API_PREFIX}{path}"

    def check(self, response: httpx.Response, feature: str) -> httpx.Response:
        if response.status_code >= 400:
            raise error_for(response, feature, signed_in=bool(self.token), server=self.url)
        return response

    @property
    def web_url(self) -> str:
        return self.url


class Api(_Base):
    """Synchronous client used by the subcommands."""

    def __init__(
        self, url: str, token: str | None, *, transport: httpx.BaseTransport | None = None, timeout: float = 30.0
    ) -> None:
        super().__init__(url, token)
        self.http = httpx.Client(
            base_url=self.url + API_PREFIX,
            headers=self.headers,
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> Api:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Params | None = None,
        json: Any = None,
        feature: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = self.http.request(method, path, params=_clean(params), json=json, **kwargs)
        except httpx.HTTPError as exc:
            raise _network_error(exc, self.url) from exc
        return self.check(response, feature or self.feature(method, path))

    def get(self, path: str, params: Params | None = None, *, feature: str | None = None) -> Any:
        return _decode(self.request("GET", path, params=params, feature=feature))

    def post(self, path: str, json: Any = None, *, feature: str | None = None, **kwargs: Any) -> Any:
        if json is None and "files" not in kwargs and "data" not in kwargs:
            json = {}
        return _decode(self.request("POST", path, json=json, feature=feature, **kwargs))

    def patch(self, path: str, json: Any, *, feature: str | None = None) -> Any:
        return _decode(self.request("PATCH", path, json=json, feature=feature))

    def put(self, path: str, json: Any, *, feature: str | None = None) -> Any:
        return _decode(self.request("PUT", path, json=json, feature=feature))

    def delete(self, path: str, *, feature: str | None = None) -> Any:
        return _decode(self.request("DELETE", path, feature=feature))

    def paginate(
        self, path: str, params: Params | None = None, *, limit: int | None = None, page_size: int = 100
    ) -> Iterator[dict[str, Any]]:
        """Follow `next_cursor` until `limit` items (or the end)."""
        cursor: str | None = None
        seen = 0
        while True:
            size = min(page_size, limit - seen) if limit else page_size
            page = self.get(path, {**(params or {}), "cursor": cursor, "limit": size})
            items = page.get("items", []) if isinstance(page, dict) else page
            for item in items:
                yield item
                seen += 1
                if limit and seen >= limit:
                    return
            cursor = page.get("next_cursor") if isinstance(page, dict) else None
            if not cursor or not items:
                return

    @contextmanager
    def stream(
        self, method: str, path: str, *, params: Params | None = None, headers: dict[str, str] | None = None
    ) -> Iterator[httpx.Response]:
        try:
            with self.http.stream(
                method, path, params=_clean(params), headers=headers, timeout=httpx.Timeout(30.0, read=None)
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise error_for(response, self.feature(method, path), signed_in=bool(self.token), server=self.url)
                yield response
        except httpx.HTTPError as exc:
            raise _network_error(exc, self.url) from exc


class AsyncApi(_Base):
    """Asynchronous twin used by `z top` and the interactive session."""

    def __init__(
        self, url: str, token: str | None, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30.0
    ) -> None:
        super().__init__(url, token)
        self.http = httpx.AsyncClient(
            base_url=self.url + API_PREFIX,
            headers=self.headers,
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Params | None = None,
        json: Any = None,
        feature: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = await self.http.request(method, path, params=_clean(params), json=json, **kwargs)
        except httpx.HTTPError as exc:
            raise _network_error(exc, self.url) from exc
        if response.status_code >= 400:
            raise error_for(
                response, feature or self.feature(method, path), signed_in=bool(self.token), server=self.url
            )
        return response

    async def get(self, path: str, params: Params | None = None, *, feature: str | None = None) -> Any:
        return _decode(await self.request("GET", path, params=params, feature=feature))

    async def post(self, path: str, json: Any = None, *, feature: str | None = None) -> Any:
        return _decode(await self.request("POST", path, json={} if json is None else json, feature=feature))

    async def items(self, path: str, params: Params | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
        page = await self.get(path, {**(params or {}), "limit": limit})
        if isinstance(page, dict):
            return list(page.get("items", []))
        return list(page or [])

    @asynccontextmanager
    async def stream(
        self, method: str, path: str, *, params: Params | None = None, headers: dict[str, str] | None = None
    ) -> AsyncIterator[httpx.Response]:
        try:
            async with self.http.stream(
                method, path, params=_clean(params), headers=headers, timeout=httpx.Timeout(30.0, read=None)
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise error_for(response, self.feature(method, path), signed_in=bool(self.token), server=self.url)
                yield response
        except httpx.HTTPError as exc:
            raise _network_error(exc, self.url) from exc


def dumps(data: Any) -> str:
    return jsonlib.dumps(data, indent=2, sort_keys=False, default=str, ensure_ascii=False)
