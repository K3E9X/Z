"""Shared plumbing for the Typer commands: error handling, `--json`, prompts, durations."""

from __future__ import annotations

import functools
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, TypeVar

import typer
from rich.prompt import Confirm, Prompt

from z_cli import output
from z_cli.errors import CliError, ExitCode, UsageError

F = TypeVar("F", bound=Callable[..., Any])

JsonOpt = Annotated[bool, typer.Option("--json", help="Print the API's JSON (contract field names) instead of text.")]
LimitOpt = Annotated[int, typer.Option("--limit", "-n", min=1, help="At most this many items.")]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")]


def command(app: typer.Typer, name: str | None = None, **kwargs: Any) -> Callable[[F], F]:
    """Register `fn` on `app`; CliError becomes a readable message and the documented exit code."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kw: Any) -> Any:
            as_json = bool(kw.get("as_json")) or kw.get("output_format") in {"json", "stream-json"}
            try:
                result = fn(*args, **kw)
            except CliError as exc:
                output.report_error(exc, as_json)
                raise typer.Exit(int(exc.exit_code)) from None
            except KeyboardInterrupt:
                output.note("interrupted")
                raise typer.Exit(130) from None
            if isinstance(result, int) and result != 0:
                raise typer.Exit(int(result))
            return None

        app.command(name, **kwargs)(wrapper)
        return fn

    return decorator


def guarded(fn: Callable[..., Any], *args: Any, as_json: bool = False) -> Any:
    """Run `fn` outside a registered command (callbacks): same error handling as `command`."""
    try:
        return fn(*args)
    except CliError as exc:
        output.report_error(exc, as_json)
        raise typer.Exit(int(exc.exit_code)) from None
    except KeyboardInterrupt:
        output.note("interrupted")
        raise typer.Exit(130) from None


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def confirm(question: str, *, yes: bool = False, default: bool = False) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        raise UsageError(f"{question} (needs confirmation)", hint="Run it in a terminal, or pass --yes.")
    return bool(Confirm.ask(question, default=default, console=output.err()))


def ask(question: str, *, default: str | None = None, password: bool = False) -> str:
    if not sys.stdin.isatty():
        raise UsageError(f"{question} (needs an answer)", hint="Run it in a terminal, or pass the value as an option.")
    if default is None:
        return str(Prompt.ask(question, console=output.err(), password=password))
    return str(Prompt.ask(question, console=output.err(), password=password, default=default))


_DURATION = re.compile(r"^(\d+)\s*([smhdw])$")


def parse_duration(text: str) -> timedelta:
    match = _DURATION.match(text.strip().lower())
    if not match:
        raise UsageError(f"bad duration {text!r}", hint="Use a number and a unit: 30m, 12h, 7d, 2w.")
    n, unit = int(match.group(1)), match.group(2)
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
    }[unit]


def since_param(text: str | None) -> str | None:
    """`7d` -> an RFC 3339 timestamp 7 days ago; an RFC 3339 value is passed through."""
    if not text:
        return None
    try:
        return (datetime.now(UTC) - parse_duration(text)).isoformat().replace("+00:00", "Z")
    except UsageError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
        except ValueError:
            raise UsageError(f"bad time {text!r}", hint="Use a duration (7d) or an RFC 3339 timestamp.") from None


def exit_code_for_mission(status: str) -> ExitCode:
    if status == "succeeded":
        return ExitCode.OK
    if status == "budget_exceeded":
        return ExitCode.BUDGET
    if status in {"waiting", "planned", "paused"}:
        return ExitCode.ATTENTION
    if status in {"failed", "cancelled", "timed_out"}:
        return ExitCode.FAILED
    return ExitCode.OK
