"""Draft `graphql` declared operations from an introspection result (docs/05 §6, ADR 0020).

One operation per field of the query type (`<field>.query`, a read) and per field of the mutation type
(`mutations.<field>`, classed by its verb: at least a write). Subscriptions are never drafted. Each document
selects the scalar and enum fields of the result, two object levels deep at most, and takes the field's
arguments as variables, so the agent only fills variables of a reviewed document. Everything here is a draft:
an admin reviews every document and its class before anything is exposed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from z_common.models.enums import OperationClass
from z_common.packs.classify import READ_VERBS, classify

INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types {
      kind name description
      fields(includeDeprecated: false) {
        name description
        args { name description type { ...TypeRef } defaultValue }
        type { ...TypeRef }
      }
      inputFields { name type { ...TypeRef } defaultValue }
      enumValues(includeDeprecated: false) { name }
    }
  }
}
fragment TypeRef on __Type {
  kind name
  ofType { kind name ofType { kind name ofType { kind name ofType { kind name ofType { kind name } } } } }
}
"""
MAX_DEPTH = 2  # object levels below the root field
MAX_FIELDS = 40  # leaves per document
MAX_OPERATIONS = 200
SCALARS = {"Int": "integer", "Float": "number", "String": "string", "ID": "string", "Boolean": "boolean"}


class IntrospectionError(ValueError):
    pass


@dataclass
class Draft:
    declared: list[dict[str, Any]]
    warnings: list[str]


def schema_of(raw: dict[str, Any] | str) -> dict[str, Any]:
    """The `__schema` object of an introspection result ({data: {__schema}}, {__schema} or the JSON text)."""
    doc: Any = raw
    if isinstance(raw, str):
        try:
            doc = json.loads(raw)
        except ValueError as exc:
            raise IntrospectionError("not JSON: paste the introspection result (SDL is not supported)") from exc
    if isinstance(doc, dict) and isinstance(doc.get("data"), dict):
        doc = doc["data"]
    schema = doc.get("__schema") if isinstance(doc, dict) else None
    if not isinstance(schema, dict) or not isinstance(schema.get("types"), list):
        raise IntrospectionError("not an introspection result (__schema with types required)")
    return schema


def snake(name: str) -> str:
    out = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    out = re.sub(r"(?<=[A-Z])([A-Z][a-z])", r"_\1", out)
    return re.sub(r"[^a-z0-9_]+", "_", out.lower()).strip("_") or "field"


def named(ref: dict[str, Any] | None) -> dict[str, Any]:
    while ref and ref.get("ofType") is not None and ref.get("kind") in {"NON_NULL", "LIST"}:
        ref = ref["ofType"]
    return ref or {}


def type_text(ref: dict[str, Any]) -> str:
    """GraphQL type syntax of a type reference: `[Issue!]!`."""
    kind = ref.get("kind")
    if kind == "NON_NULL":
        return type_text(ref["ofType"]) + "!"
    if kind == "LIST":
        return "[" + type_text(ref["ofType"]) + "]"
    return str(ref.get("name"))


def json_schema(ref: dict[str, Any], types: dict[str, dict[str, Any]]) -> dict[str, Any]:
    kind = ref.get("kind")
    if kind == "NON_NULL":
        return json_schema(ref["ofType"], types)
    if kind == "LIST":
        return {"type": "array", "items": json_schema(ref["ofType"], types)}
    name = str(ref.get("name"))
    if kind == "ENUM":
        values = [v["name"] for v in (types.get(name) or {}).get("enumValues") or []]
        return {"type": "string", "enum": values} if values else {"type": "string"}
    if kind == "INPUT_OBJECT":
        return {"type": "object", "description": f"GraphQL input {name}"}
    return {"type": SCALARS[name]} if name in SCALARS else {"description": f"GraphQL scalar {name}"}


def selection(type_name: str, types: dict[str, dict[str, Any]], depth: int, budget: list[int]) -> str:
    """Selection set of an object type: its leaves, and object fields without required arguments while depth
    remains. Interfaces and unions select `__typename` only (no fragments are guessed)."""
    t = types.get(type_name) or {}
    if t.get("kind") in {"INTERFACE", "UNION"} and not t.get("fields"):
        return "{ __typename }"
    parts: list[str] = []
    for field in t.get("fields") or []:
        if budget[0] <= 0:
            break
        if any(a["type"].get("kind") == "NON_NULL" and a.get("defaultValue") is None for a in field.get("args") or []):
            continue  # needs an argument: left out of nested selections
        inner = named(field["type"])
        if inner.get("kind") in {"SCALAR", "ENUM"}:
            parts.append(field["name"])
            budget[0] -= 1
        elif inner.get("kind") in {"OBJECT", "INTERFACE", "UNION"} and depth < MAX_DEPTH:
            sub = selection(str(inner.get("name")), types, depth + 1, budget)
            if sub != "{ __typename }":
                parts.append(f"{field['name']} {sub}")
    return "{ " + " ".join(parts) + " }" if parts else "{ __typename }"


def mutation_id(field_name: str) -> str:
    """`mutations.<name>` whose verb is never read-like: a mutation is at least a write (a read id would refuse
    its own document). Read-verb tokens are dropped from the name; destructive verbs stay."""
    tokens = [t for t in snake(field_name).split("_") if t not in READ_VERBS]
    return "mutations." + ("_".join(tokens) or "run")


def draft(raw: dict[str, Any] | str) -> Draft:
    schema = schema_of(raw)
    types = {str(t.get("name")): t for t in schema["types"] if isinstance(t, dict)}
    warnings: list[str] = []
    declared: list[dict[str, Any]] = []
    roots = [
        ("query", (schema.get("queryType") or {}).get("name")),
        ("mutation", (schema.get("mutationType") or {}).get("name")),
    ]
    seen: set[str] = set()
    for op_type, root in roots:
        fields = (types.get(str(root)) or {}).get("fields") if root else None
        for field in fields or []:
            if len(declared) >= MAX_OPERATIONS:
                warnings.append(f"only the first {MAX_OPERATIONS} fields were drafted")
                break
            name = str(field["name"])
            if name.startswith("__"):
                continue
            local = f"{snake(name)}.query" if op_type == "query" else mutation_id(name)
            if local in seen:
                warnings.append(f"{op_type} field {name}: its id {local} is taken; rename it before saving")
                continue
            seen.add(local)
            args = field.get("args") or []
            variables = ", ".join(f"${a['name']}: {type_text(a['type'])}" for a in args)
            call = name + ("(" + ", ".join(f"{a['name']}: ${a['name']}" for a in args) + ")" if args else "")
            inner = named(field["type"])
            leaf = inner.get("kind") in {"SCALAR", "ENUM"}
            select = "" if leaf else " " + selection(str(inner.get("name")), types, 1, [MAX_FIELDS])
            operation_name = name[:1].upper() + name[1:]
            header = f"{op_type} {operation_name}" + (f"({variables})" if variables else "")
            properties = {a["name"]: json_schema(a["type"], types) for a in args}
            for a in args:
                if a.get("description"):
                    properties[a["name"]]["description"] = str(a["description"])[:300]
            required = sorted(
                a["name"] for a in args if a["type"].get("kind") == "NON_NULL" and a.get("defaultValue") is None
            )
            input_schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
            if required:
                input_schema["required"] = required
            op: dict[str, Any] = {
                "id": local,
                "description": str(field.get("description") or f"GraphQL {op_type} {name}")[:500],
                "query": f"{header} {{ {call}{select} }}",
                "input_schema": input_schema,
            }
            if op_type == "mutation" and classify(local, None) == OperationClass.read:
                raise IntrospectionError(f"mutation {name} would be drafted as a read")
            declared.append(op)
    if not declared:
        raise IntrospectionError("the schema has no query or mutation fields")
    if (schema.get("subscriptionType") or {}).get("name"):
        warnings.append("subscriptions are never drafted (Z calls operations, it does not hold streams)")
    return Draft(declared, warnings)
