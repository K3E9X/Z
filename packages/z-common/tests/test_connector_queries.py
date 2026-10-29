from __future__ import annotations

import pytest
from z_common.connectors.queries import QueryError, check_select, connector_problems, graphql_writes


def test_graphql_writes_ignores_strings_and_comments() -> None:
    assert graphql_writes("mutation { addTag(id: 1) { id } }")
    assert graphql_writes("query A { a } mutation B { b }")
    assert graphql_writes("subscription { events { id } }")
    assert not graphql_writes('query { assets(note: "mutation inside a string") { id } }')
    assert not graphql_writes("query { assets { id } } # mutation in a comment")
    assert not graphql_writes('query { a(x: """block\nmutation""") }')
    assert not graphql_writes("{ mutationCount }")


@pytest.mark.parametrize(
    ("query", "params"),
    [
        ("SELECT id, owner FROM assets WHERE env = %(env)s", ["env"]),
        ("with t as (select 1) select * from t;", []),
        ("SELECT 'it''s; DELETE' AS s, %(a)s, %(b)s -- delete in a comment", ["a", "b"]),
        ("SELECT comment, set_name FROM notes WHERE pct LIKE '50%%'", []),
    ],
)
def test_check_select_accepts(query: str, params: list[str]) -> None:
    assert check_select(query) == params


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("DELETE FROM assets", "starts with SELECT or WITH"),
        ("SELECT 1; DELETE FROM assets", "one statement"),
        ("WITH x AS (DELETE FROM assets RETURNING *) SELECT * FROM x", "cannot contain DELETE"),
        ("SELECT * FROM assets FOR UPDATE", "cannot contain UPDATE"),
        ("SELECT * FROM t WHERE a LIKE '%' || %s", "parameters are written"),
        ("SELECT * FROM t WHERE a LIKE '50%'", "also inside strings"),
    ],
)
def test_check_select_refuses(query: str, message: str) -> None:
    with pytest.raises(QueryError, match=message):
        check_select(query)


def test_connector_problems() -> None:
    gql = {
        "kind": "graphql",
        "declared": [
            {"id": "assets.search", "query": "mutation { drop }"},
            {"id": "assets.tag", "query": "mutation { tag }"},  # a write may mutate
            {"id": "assets.get"},
        ],
    }
    assert connector_problems(gql) == [
        "operation assets.search: a read operation cannot contain a mutation or subscription",
        "operation assets.get: graphql operations declare a query",
    ]
    db = {
        "kind": "database",
        "declared": [
            {"id": "assets.search", "query": "SELECT * FROM a WHERE env = %(env)s", "input_schema": {"properties": {}}},
            {"id": "assets.wipe", "query": "TRUNCATE a"},
        ],
    }
    assert connector_problems(db) == [
        "operation assets.search: the query uses undeclared parameters env",
        "operation assets.wipe: a declared query starts with SELECT or WITH",
    ]
    assert connector_problems({"kind": "rest"}) == []
