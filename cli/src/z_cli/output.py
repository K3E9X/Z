"""Consoles, JSON output and error reporting shared by every command."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any

from rich.console import Console, RenderableType
from rich.table import Table
from rich.text import Text

from z_cli.errors import CliError
from z_cli.theme import rich_theme

_theme_name: str | None = None
_out: Console | None = None
_err: Console | None = None


def configure(theme: str | None = None, *, no_color: bool = False) -> None:
    global _theme_name, _out, _err
    _theme_name = theme
    color = None if not (no_color or os.environ.get("NO_COLOR")) else "none"
    kwargs: dict[str, Any] = {"theme": rich_theme(theme), "highlight": False, "soft_wrap": False}
    if color == "none":
        kwargs["no_color"] = True
    _out = Console(**kwargs)
    _err = Console(stderr=True, **kwargs)


def out() -> Console:
    if _out is None:
        configure(_theme_name)
    assert _out is not None
    return _out


def err() -> Console:
    if _err is None:
        configure(_theme_name)
    assert _err is not None
    return _err


def reset() -> None:
    """Forget the consoles (tests swap sys.stdout between invocations)."""
    global _out, _err
    _out = _err = None


def print_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, indent=2, default=str, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def print_jsonl(data: Any) -> None:
    sys.stdout.write(json.dumps(data, separators=(",", ":"), default=str, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def emit(data: Any, as_json: bool, render: Callable[[Any], RenderableType | None]) -> None:
    if as_json:
        print_json(data)
        return
    renderable = render(data)
    if renderable is not None:
        out().print(renderable)


def report_error(exc: CliError, as_json: bool = False) -> None:
    if as_json:
        sys.stderr.write(json.dumps(exc.as_json(), default=str) + "\n")
        return
    console = err()
    message = Text()
    message.append("error ", style="z.err")
    message.append(exc.message)
    console.print(message)
    if exc.hint:
        console.print(Text(exc.hint, style="z.d"))


def note(text: str, style: str = "z.d") -> None:
    err().print(Text(text, style=style))


def success(text: str) -> None:
    line = Text()
    line.append("● ", style="z.ok")
    line.append(text)
    out().print(line)


def table(columns: Sequence[str | tuple[str, dict[str, Any]]], rows: Sequence[Sequence[RenderableType]]) -> Table:
    t = Table(box=None, pad_edge=False, header_style="z.d", show_edge=False, expand=False, padding=(0, 2, 0, 0))
    for column in columns:
        if isinstance(column, tuple):
            t.add_column(column[0].upper(), **column[1])
        else:
            t.add_column(column.upper(), overflow="fold")
    for row in rows:
        t.add_row(*row)
    return t


def kv(pairs: Sequence[tuple[str, RenderableType | None]]) -> Table:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="z.d", no_wrap=True)
    t.add_column()
    for key, value in pairs:
        if value is None or value == "":
            continue
        t.add_row(key, value)
    return t
