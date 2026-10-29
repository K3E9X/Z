"""Drafting graphql declared operations from introspection (docs/05 §6, ADR 0020)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from z_common.connectors.graphql_import import IntrospectionError, draft, mutation_id, schema_of, snake
from z_common.connectors.queries import connector_problems
from z_common.models.enums import OperationClass
from z_common.packs.classify import classify


def t(kind: str, name: str | None = None, of: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": kind, "name": name, "ofType": of}


def f(field: str, type_: dict[str, Any], **args: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": field,
        "args": [{"name": k, "type": v, "defaultValue": None} for k, v in args.items()],
        "type": type_,
    }


STR, INT = t("SCALAR", "String"), t("SCALAR", "Int")


def schema(query: list[dict[str, Any]], mutation: list[dict[str, Any]] | None = None, *extra: dict) -> dict:
    types = [{"kind": "OBJECT", "name": "Query", "fields": query}, *extra]
    if mutation is not None:
        types.append({"kind": "OBJECT", "name": "Mutation", "fields": mutation})
    return {
        "__schema": {
            "queryType": {"name": "Query"},
            "mutationType": {"name": "Mutation"} if mutation is not None else None,
            "types": types,
        }
    }


def test_snake_case_and_mutation_ids_are_never_reads() -> None:
    assert snake("mergeRequests") == "merge_requests" and snake("HTMLPage") == "html_page"
    assert mutation_id("createIssue") == "mutations.create_issue"
    assert mutation_id("getOrCreateTag") == "mutations.or_create_tag"
    assert mutation_id("fetch") == "mutations.run"
    assert classify(mutation_id("deleteProject")) == OperationClass.destructive
    for name in ("listAndArchive", "searchReindex", "getToken", "count"):
        assert classify(mutation_id(name)) != OperationClass.read


def test_documents_select_leaves_two_levels_deep_and_type_their_variables() -> None:
    issue = {
        "kind": "OBJECT",
        "name": "Issue",
        "fields": [
            f("id", t("NON_NULL", None, t("SCALAR", "ID"))),
            f("state", t("ENUM", "State")),
            f("project", t("OBJECT", "Project")),
            f("notes", t("LIST", None, t("OBJECT", "Note")), first=t("NON_NULL", None, INT)),
            f("author", t("UNION", "Actor")),
        ],
    }
    project = {"kind": "OBJECT", "name": "Project", "fields": [f("path", STR), f("group", t("OBJECT", "Group"))]}
    group = {"kind": "OBJECT", "name": "Group", "fields": [f("name", STR)]}
    state = {"kind": "ENUM", "name": "State", "enumValues": [{"name": "opened"}, {"name": "closed"}]}
    actor = {"kind": "UNION", "name": "Actor", "fields": None}
    filt = {"kind": "INPUT_OBJECT", "name": "IssueFilter", "inputFields": []}
    doc = schema(
        [
            f(
                "issues",
                t("LIST", None, t("OBJECT", "Issue")),
                state=t("ENUM", "State"),
                labels=t("LIST", None, t("NON_NULL", None, STR)),
                filter=t("INPUT_OBJECT", "IssueFilter"),
                first=t("NON_NULL", None, INT),
            ),
            f("version", STR),
        ],
        None,
        issue,
        project,
        group,
        state,
        actor,
        filt,
    )
    out = draft(json.dumps({"data": doc}))
    issues, version = out.declared
    assert issues["id"] == "issues.query" and version["id"] == "version.query"
    assert issues["query"] == (
        "query Issues($state: State, $labels: [String!], $filter: IssueFilter, $first: Int!) "
        "{ issues(state: $state, labels: $labels, filter: $filter, first: $first) "
        "{ id state project { path } } }"
    )  # group is a third level; notes needs an argument; the union has no fields to guess
    assert issues["input_schema"] == {
        "type": "object",
        "additionalProperties": False,
        "required": ["first"],
        "properties": {
            "state": {"type": "string", "enum": ["opened", "closed"]},
            "labels": {"type": "array", "items": {"type": "string"}},
            "filter": {"type": "object", "description": "GraphQL input IssueFilter"},
            "first": {"type": "integer"},
        },
    }
    assert version["query"] == "query Version { version }"
    assert connector_problems({"kind": "graphql", "declared": out.declared}) == []


def test_mutations_are_writes_and_pass_the_connector_checks() -> None:
    out = draft(
        schema(
            [f("me", STR)],
            [f("getOrCreateTag", STR, tag=t("NON_NULL", None, STR)), f("deleteTag", STR, id=STR)],
        )
    )
    ids = [o["id"] for o in out.declared]
    assert ids == ["me.query", "mutations.or_create_tag", "mutations.delete_tag"]
    assert [classify(i) for i in ids] == [OperationClass.read, OperationClass.write, OperationClass.destructive]
    assert out.declared[1]["query"].startswith("mutation GetOrCreateTag($tag: String!)")
    assert connector_problems({"kind": "graphql", "declared": out.declared}) == []


def test_duplicate_ids_are_reported_not_overwritten() -> None:
    out = draft(schema([f("fooBar", STR), f("foo_bar", STR)]))
    assert [o["id"] for o in out.declared] == ["foo_bar.query"]
    assert any("foo_bar" in w and "taken" in w for w in out.warnings)


@pytest.mark.parametrize(
    "raw",
    ["type Query { a: Int }", {"data": {"assets": []}}, {"__schema": {"types": "x"}}],
)
def test_only_introspection_results_are_accepted(raw: Any) -> None:
    with pytest.raises(IntrospectionError):
        schema_of(raw)


def test_a_schema_without_operations_is_refused() -> None:
    with pytest.raises(IntrospectionError, match="no query or mutation"):
        draft({"__schema": {"queryType": {"name": "Query"}, "types": [{"kind": "OBJECT", "name": "Query"}]}})
