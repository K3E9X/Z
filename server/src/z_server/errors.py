"""RFC 9457 problem details. Every error response of the API goes through here (contracts/README.md)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("z.errors")
MEDIA = "application/problem+json"

TITLES = {
    "unauthenticated": "Sign-in required",
    "step-up-required": "Re-authentication required",
    "forbidden": "Not allowed",
    "separation-of-duties": "Separation of duties",
    "not-found": "Not found",
    "conflict": "Conflict",
    "locked": "Locked by the platform",
    "validation": "Invalid request",
    "rate-limited": "Too many requests",
    "budget-exceeded": "Budget exceeded",
    "unavailable": "Service unavailable",
    "internal": "Internal error",
}
STATUS = {
    "unauthenticated": 401,
    "step-up-required": 401,
    "forbidden": 403,
    "separation-of-duties": 403,
    "not-found": 404,
    "conflict": 409,
    "locked": 409,
    "validation": 422,
    "rate-limited": 429,
    "budget-exceeded": 402,
    "unavailable": 503,
    "internal": 500,
}


class Problem(Exception):
    def __init__(self, kind: str, detail: str | None = None, *, status: int | None = None, **extra: Any) -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.status = status or STATUS[kind]
        self.detail = detail
        self.extra = extra

    def body(self, request_id: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"type": f"urn:z:problem:{self.kind}", "title": TITLES[self.kind], "status": self.status}
        if self.detail:
            out["detail"] = self.detail
        if request_id:
            out["request_id"] = request_id
        out.update({k: v for k, v in self.extra.items() if v is not None})
        return out


def problem_response(problem: Problem, request: Request) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if problem.status == 401 else None
    body = problem.body(getattr(request.state, "request_id", None))
    return JSONResponse(body, status_code=problem.status, media_type=MEDIA, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def _problem(request: Request, exc: Problem) -> JSONResponse:
        return problem_response(exc, request)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"path": "/".join(str(p) for p in err.get("loc", ()) if p != "body"), "message": str(err.get("msg"))}
            for err in exc.errors()
        ]
        return problem_response(
            Problem("validation", "The request does not match the contract", errors=errors), request
        )

    @app.exception_handler(ValidationError)
    async def _model_validation(request: Request, exc: ValidationError) -> JSONResponse:
        errors = [
            {"path": "/".join(str(p) for p in err.get("loc", ())), "message": str(err.get("msg"))}
            for err in exc.errors()
        ]
        return problem_response(
            Problem("validation", "The document does not match the contract", errors=errors), request
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        kind = {
            401: "unauthenticated",
            403: "forbidden",
            404: "not-found",
            405: "not-found",
            409: "conflict",
            429: "rate-limited",
        }.get(exc.status_code, "validation" if 400 <= exc.status_code < 500 else "internal")
        return problem_response(Problem(kind, str(exc.detail) if exc.detail else None, status=exc.status_code), request)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"request_id": getattr(request.state, "request_id", None)})
        return problem_response(Problem("internal"), request)  # never a stack trace
