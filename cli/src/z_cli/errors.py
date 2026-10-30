"""Errors and exit codes of `z` (docs/10 §3).

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | failure: the mission failed, was cancelled or timed out, or the server refused the request |
| 2 | usage error: bad arguments, or input the server rejected as invalid (422) |
| 3 | needs attention: a mission waits for an approval or an answer (headless mode stops there) |
| 4 | auth: not signed in, session expired or revoked, permission missing, step-up required |
| 5 | budget exceeded |
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

from z_common.models.common import Problem


class ExitCode(IntEnum):
    OK = 0
    FAILED = 1
    USAGE = 2
    ATTENTION = 3
    AUTH = 4
    BUDGET = 5


class CliError(Exception):
    """An error shown to the user as one short message (plus an optional hint), then exit with `exit_code`."""

    def __init__(self, message: str, *, exit_code: ExitCode = ExitCode.FAILED, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.hint = hint

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.message, "exit_code": int(self.exit_code)}
        if self.hint:
            out["hint"] = self.hint
        return out


class UsageError(CliError):
    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message, exit_code=ExitCode.USAGE, hint=hint)


class ApiError(CliError):
    """The server answered with an RFC 9457 problem."""

    def __init__(
        self,
        status: int,
        problem: Problem,
        *,
        exit_code: ExitCode = ExitCode.FAILED,
        hint: str | None = None,
        message: str | None = None,
    ) -> None:
        text = message or (f"{problem.title}: {problem.detail}" if problem.detail else problem.title)
        super().__init__(text, exit_code=exit_code, hint=hint)
        self.status = status
        self.problem = problem

    @property
    def kind(self) -> str:
        return self.problem.type.removeprefix("urn:z:problem:")

    def as_json(self) -> dict[str, Any]:
        out = super().as_json()
        out["status"] = self.status
        out["problem"] = self.problem.dump()
        return out


class AuthRequired(ApiError):
    """401: no token, or the token expired or was revoked."""


class Forbidden(ApiError):
    """403: a permission is missing (or separation of duties)."""


class StepUpRequired(ApiError):
    """401 step-up-required: the action needs a fresh authentication."""


class NotAvailable(CliError):
    """The contract defines the endpoint but this server does not serve it yet (404/405 on the route itself)."""

    def __init__(self, feature: str) -> None:
        super().__init__(
            f"{feature} is not available on this server yet.",
            exit_code=ExitCode.FAILED,
            hint="The API contract defines it; the server you are connected to does not implement it yet.",
        )
        self.feature = feature


class NotSignedIn(CliError):
    def __init__(self, url: str) -> None:
        super().__init__(
            f"Not signed in to {url}.", exit_code=ExitCode.AUTH, hint="Run `z login` (or set Z_TOKEN) and try again."
        )


class ContractMismatch(CliError):
    def __init__(self, what: str, detail: str) -> None:
        super().__init__(
            f"The server's {what} does not match the contract this CLI was built for: {detail}",
            hint="Upgrade z, or use --json to see the raw response.",
        )
