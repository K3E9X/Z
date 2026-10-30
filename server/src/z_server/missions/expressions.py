"""Safe evaluation of `when` expressions (contracts/README.md "Expressions").

Allowed: literals, names `inputs`, `item`, `steps`, attribute and index access on dicts/lists, comparisons
(== != < <= > >= in, not in), boolean and/or/not, and calls `<step>_for(item)` returning the output of
`<step>` for the same fan-out item. Nothing else (no other calls, no dunder access, no comprehensions).
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from typing import Any

MAX_LENGTH = 500
COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


class ExpressionError(ValueError):
    pass


class Missing:
    """Result of reading a missing key: compares unequal to everything, falsy."""

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Missing)

    def __hash__(self) -> int:
        return 0


MISSING = Missing()


def _get(value: Any, key: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, MISSING)
    if isinstance(value, list) and isinstance(key, int):
        return value[key] if -len(value) <= key < len(value) else MISSING
    return MISSING


def evaluate(expression: str, names: Mapping[str, Any], step_for: Callable[[str, Any], Any] | None = None) -> Any:
    if len(expression) > MAX_LENGTH:
        raise ExpressionError("expression too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"invalid expression: {exc.msg}") from exc

    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, str | int | float | bool | type(None)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in {"true", "false", "null"}:
                return {"true": True, "false": False, "null": None}[node.id]
            if node.id not in names:
                raise ExpressionError(f"unknown name {node.id!r}")
            return names[node.id]
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise ExpressionError("private attributes are not allowed")
            return _get(ev(node.value), node.attr)
        if isinstance(node, ast.Subscript):
            return _get(ev(node.value), ev(node.slice))
        if isinstance(node, ast.List | ast.Tuple):
            return [ev(e) for e in node.elts]
        if isinstance(node, ast.BoolOp):
            values = [ev(v) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not ev(node.operand)
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comparator in zip(node.ops, node.comparators, strict=True):
                right = ev(comparator)
                try:
                    ok = COMPARE[type(op)](left, right)
                except TypeError:
                    ok = False
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            if (
                not isinstance(node.func, ast.Name)
                or not node.func.id.endswith("_for")
                or node.keywords
                or len(node.args) != 1
            ):
                raise ExpressionError("only <step>_for(item) calls are allowed")
            if step_for is None:
                raise ExpressionError(f"{node.func.id}() is only available in fan-out steps")
            return step_for(node.func.id.removesuffix("_for"), ev(node.args[0]))
        raise ExpressionError(f"{type(node).__name__} is not allowed")

    return ev(tree)


def truthy(expression: str, names: Mapping[str, Any], step_for: Callable[[str, Any], Any] | None = None) -> bool:
    return bool(evaluate(expression, names, step_for))
