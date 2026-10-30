"""Consoles, JSON output and error reporting shared by every command."""

from __future__ import annotations

import json
import os
import re
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


ID = re.compile(r"^[a-z]{1,5}_[0-9A-Za-z]{10,}$|^[a-z]+(\.[a-z0-9_]+){2,}$")


def _plain(cell: RenderableType) -> str:
    return cell.plain if isinstance(cell, Text) else cell if isinstance(cell, str) else ""


SHORT = 12  # columns this narrow (statuses, counts, dates) are never shortened
GAP = 2


def table(
    columns: Sequence[str | tuple[str, dict[str, Any]]],
    rows: Sequence[Sequence[RenderableType]],
    *,
    width: int | None = None,
) -> Table:
    """One line per row, fitted to the terminal: long free-text cells end with an ellipsis; ids, operation ids
    and short cells are never cut (ids are what people copy into the next command)."""
    names: list[str] = []
    options_list: list[dict[str, Any]] = []
    natural: list[int] = []
    fixed: list[bool] = []
    ids: list[bool] = []
    for i, column in enumerate(columns):
        name, options = (column[0], dict(column[1])) if isinstance(column, tuple) else (column, {})
        values = [_plain(row[i]) for row in rows if i < len(row)]
        filled = [v for v in values if v]
        longest = max([len(name), *(len(v) for v in values)]) if values else len(name)
        is_id = bool(filled) and all(ID.match(v) for v in filled)
        names.append(name)
        options_list.append(options)
        natural.append(longest if is_id else min(longest, 60))
        fixed.append(is_id or longest <= SHORT)
        ids.append(is_id)
    available = (width or out().width) - GAP * len(columns)
    widths = list(natural)
    while sum(widths) > available:
        flexible = [i for i, w in enumerate(widths) if not fixed[i] and w > 8]
        if not flexible:
            break
        widest = max(flexible, key=lambda i: widths[i])
        widths[widest] -= 1
    t = Table(box=None, pad_edge=False, header_style="z.d", show_edge=False, expand=False, padding=(0, GAP, 0, 0))
    for name, options, w, is_id in zip(names, options_list, widths, ids, strict=True):
        options.setdefault("no_wrap", True)
        options.setdefault("overflow", "ellipsis")
        options.setdefault("width", w)
        if is_id:
            options.setdefault("min_width", w)
        t.add_column(name.upper(), **options)
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
