"""Tiny JQL / CQL subset: `field op value` clauses joined by AND / OR (AND binds tighter), ORDER BY ignored.

Operators: =, !=, ~ (case-insensitive contains), !~, in (...), not in (...), is EMPTY, is not EMPTY, and
<, <=, >, >= on dates (values: `-30d`, `-4h`, `-2w`, `now()`, `"2026-10-01"` or an ISO timestamp).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

Accessor = Callable[[str], list[str] | None]  # field -> values, None = unknown field

_CLAUSE = re.compile(
    r"""^\s*(?P<field>[\w.]+)\s*
        (?P<op><=|>=|<|>|!=|!~|=|~|\bnot\s+in\b|\bin\b|\bis\s+not\b|\bis\b)\s*
        (?P<value>\([^)]*\)|"[^"]*"|'[^']*'|\S+)\s*$""",
    re.I | re.X,
)


class QueryError(ValueError):
    pass


def _split(text: str, word: str) -> list[str]:
    parts, depth, quote, start, i = [], 0, "", 0, 0
    pattern = re.compile(rf"\s+{word}\s+", re.I)
    while i < len(text):
        ch = text[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and (m := pattern.match(text, i)):
            parts.append(text[start:i])
            start = i = m.end()
            continue
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _unquote(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value


def _clause(clause: str, get: Accessor) -> bool:
    m = _CLAUSE.match(clause)
    if not m:
        raise QueryError(f"cannot parse clause: {clause!r}")
    field, op, raw = m.group("field").lower(), re.sub(r"\s+", " ", m.group("op").lower()), m.group("value")
    values = get(field)
    if values is None:
        raise QueryError(f"Field '{field}' does not exist or you do not have permission to view it.")
    if op in ("<", "<=", ">", ">="):
        bound = _when(_unquote(raw))
        dates = [d for d in (_when(v) for v in values if v) if d is not None]
        if bound is None:
            raise QueryError(f"cannot read the date {raw!r}")
        compare = {
            "<": lambda a: a < bound,
            "<=": lambda a: a <= bound,
            ">": lambda a: a > bound,
            ">=": lambda a: a >= bound,
        }
        return any(compare[op](d) for d in dates)
    have = [v.lower() for v in values]
    if op in ("is", "is not"):
        empty = not [v for v in have if v not in ("", "unresolved")]
        return empty if op == "is" else not empty
    if op in ("in", "not in"):
        wanted = [_unquote(x).lower() for x in raw.strip("()").split(",") if x.strip()]
        hit = any(v in have for v in wanted)
        return hit if op == "in" else not hit
    target = _unquote(raw).lower()
    if op in ("~", "!~"):
        hit = any(target in v for v in have)
        return hit if op == "~" else not hit
    hit = target in have
    return hit if op == "=" else not hit


def _when(value: str) -> datetime | None:
    value = value.strip().lower()
    now = datetime.now(UTC)
    if value in ("now()", "now"):
        return now
    m = re.fullmatch(r"([-+]?\d+)([mhdw])", value)
    if m:
        unit = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[m.group(2)]
        return now + timedelta(**{unit: int(m.group(1))})
    try:
        parsed = datetime.fromisoformat(value.upper().replace("Z", "+00:00").replace(".000+0000", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def matches(query: str, get: Accessor) -> bool:
    query = re.split(r"\border\s+by\b", query, flags=re.I)[0].strip()
    if not query:
        return True
    return any(all(_clause(c, get) for c in _split(group, "AND")) for group in _split(query, "OR"))
