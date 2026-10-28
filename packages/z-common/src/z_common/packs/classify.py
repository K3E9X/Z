"""Platform classifier for connector operations (docs/05 §3). It is a floor: rules may raise, never lower.

The verb is the last segment of the local operation id (`merge_requests.merge` -> `merge`), split on `_`
so `remove_member` counts as `remove`. Resource names never make an operation destructive
(`merge_requests.create` is a write).
"""

from __future__ import annotations

from z_common.models.enums import OperationClass

DESTRUCTIVE_VERBS = frozenset(
    {
        "delete",
        "remove",
        "purge",
        "revoke",
        "destroy",
        "disable",
        "deactivate",
        "merge",
        "deploy",
        "approve",
        "transfer",
    }
)
READ_VERBS = frozenset(
    {
        "get",
        "list",
        "search",
        "read",
        "fetch",
        "query",
        "diff",
        "show",
        "describe",
        "count",
        "find",
        "lookup",
        "download",
        "status",
    }
)
ORDER = {OperationClass.read: 0, OperationClass.write: 1, OperationClass.destructive: 2}


def verb_tokens(local_id: str) -> set[str]:
    return set(local_id.rsplit(".", 1)[-1].split("_"))


def classify(
    local_id: str,
    method: str | None = None,
    read_only_hint: bool | None = None,
    *,
    require_hint: bool = False,
) -> OperationClass:
    """Class of an operation.

    - HTTP connectors pass `method`: GET/HEAD are read, DELETE is destructive, others write.
    - MCP connectors pass the tool's `readOnlyHint`; with `require_hint=True` (runtime gateway) a tool
      is read only when the hint is True AND the verb is read-like. Static lint uses the verb alone.
    - A destructive verb always wins.
    """
    tokens = verb_tokens(local_id)
    if tokens & DESTRUCTIVE_VERBS or (method or "").upper() == "DELETE":
        return OperationClass.destructive
    if method is not None:
        return OperationClass.read if method.upper() in {"GET", "HEAD"} else OperationClass.write
    if read_only_hint is False or (require_hint and read_only_hint is not True):
        return OperationClass.write
    return OperationClass.read if tokens & READ_VERBS else OperationClass.write


def raise_only(floor: OperationClass, requested: OperationClass | None) -> OperationClass:
    """Combine the classifier floor with a rule class: the stricter one wins."""
    if requested is None:
        return floor
    return requested if ORDER[requested] > ORDER[floor] else floor


def is_glob(pattern: str) -> bool:
    return "*" in pattern


def glob_match(pattern: str, value: str) -> bool:
    """Segment-wise glob. A trailing `*` matches one or more segments; elsewhere `*` matches one segment."""
    p_parts, v_parts = pattern.split("."), value.split(".")
    for index, part in enumerate(p_parts):
        if part == "*" and index == len(p_parts) - 1:
            return len(v_parts) >= len(p_parts)
        if index >= len(v_parts) or (part != "*" and part != v_parts[index]):
            return False
    return len(p_parts) == len(v_parts)
