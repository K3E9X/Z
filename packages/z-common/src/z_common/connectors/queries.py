"""Checks on the documents of `graphql` and `database` connectors (docs/05 §1), shared by the pack linter, the
connector API and the gateway. They are conservative: when in doubt, a document is treated as a write.
"""

from __future__ import annotations

import re
from typing import Any

GRAPHQL_STRING = re.compile(r'"""(?:[^"\\]|\\.|"(?!""))*"""|"(?:[^"\\\n]|\\.)*"', re.DOTALL)
GRAPHQL_COMMENT = re.compile(r"#[^\n]*")
SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
SQL_STRING = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\$(\w*)\$.*?\$\1\$", re.DOTALL)
SQL_PARAM = re.compile(r"%\((\w+)\)s")
# Defence in depth only: the gateway runs every query in a READ ONLY transaction. `update` also catches FOR UPDATE.
SQL_WRITE_WORDS = re.compile(
    r"\b(insert|update|delete|merge|truncate|drop|alter|create|grant|revoke|copy|vacuum|refresh|reindex|"
    r"cluster|listen|notify|prepare|execute|deallocate)\b",
    re.IGNORECASE,
)


class QueryError(ValueError):
    pass


def graphql_writes(document: str) -> bool:
    """True when the document may contain a mutation or a subscription (outside strings and comments)."""
    bare = GRAPHQL_COMMENT.sub(" ", GRAPHQL_STRING.sub(" ", document))
    return re.search(r"\b(mutation|subscription)\b", bare) is not None


def check_select(query: str) -> list[str]:
    """A declared database query: one SELECT (or WITH ... SELECT) statement with %(name)s parameters and no
    write keyword anywhere outside strings. Returns the parameter names."""
    no_comments = SQL_COMMENT.sub(" ", query)
    bare = SQL_STRING.sub("''", no_comments).strip()
    if bare.endswith(";"):
        bare = bare[:-1].rstrip()
    if ";" in bare:
        raise QueryError("a declared query is one statement")
    if not re.match(r"(?is)^(select|with)\b", bare):
        raise QueryError("a declared query starts with SELECT or WITH")
    word = SQL_WRITE_WORDS.search(bare)
    if word is not None:
        raise QueryError(f"a declared query cannot contain {word.group(1).upper()}")
    # the driver reads every % as a placeholder, inside string literals too
    if re.search(r"%(?!\(\w+\)s)", no_comments.replace("%%", "")):
        raise QueryError("parameters are written %(name)s; a literal percent sign is %% (also inside strings)")
    return sorted(set(SQL_PARAM.findall(bare)))


def connector_problems(manifest: dict[str, Any]) -> list[str]:
    """graphql: every declared operation has a document, and read operations (by the platform classifier)
    contain no mutation. database: every declared operation is a valid SELECT whose parameters are declared."""
    from z_common.models.enums import OperationClass
    from z_common.packs.classify import classify

    kind = manifest.get("kind")
    if kind not in {"graphql", "database"}:
        return []
    problems = []
    for op in manifest.get("declared") or []:
        op_id, query = op.get("id"), op.get("query")
        if not query:
            problems.append(f"operation {op_id}: {kind} operations declare a query")
            continue
        if kind == "graphql":
            if classify(str(op_id), None) == OperationClass.read and graphql_writes(str(query)):
                problems.append(f"operation {op_id}: a read operation cannot contain a mutation or subscription")
            continue
        try:
            params = check_select(str(query))
        except QueryError as exc:
            problems.append(f"operation {op_id}: {exc}")
            continue
        declared = set(((op.get("input_schema") or {}).get("properties") or {}).keys())
        missing = sorted(set(params) - declared)
        if missing:
            problems.append(f"operation {op_id}: the query uses undeclared parameters {', '.join(missing)}")
    return problems
