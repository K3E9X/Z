"""A small GraphQL API (inventory of assets) for `graphql` connectors. It does not parse GraphQL: it answers the
two documents the tests declare. Read token `ro-graphql` may only query; write token `rw-graphql` may also run
the `tagAsset` mutation, recorded as a write of type `graphql.tag`. Introspection (`__schema`) is answered to any
caller with the schema below, for the connector builder's import."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .state import Store

ASSETS = [
    {"id": "a1", "name": "edge-01", "env": "prod", "owner": "netops"},
    {"id": "a2", "name": "edge-02", "env": "prod", "owner": "netops"},
    {"id": "a3", "name": "lab-01", "env": "lab", "owner": "rnd"},
]


def _t(kind: str, name: str | None = None, of: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": kind, "name": name, "ofType": of}


def _nn(of: dict[str, Any]) -> dict[str, Any]:
    return _t("NON_NULL", None, of)


def _f(name: str, type_: dict[str, Any], *args: tuple[str, dict[str, Any]], desc: str | None = None) -> dict:
    return {
        "name": name,
        "description": desc,
        "args": [{"name": a, "description": None, "type": t, "defaultValue": None} for a, t in args],
        "type": type_,
    }


S, ID = _t("SCALAR", "String"), _t("SCALAR", "ID")
ASSET, ENV = _t("OBJECT", "Asset"), _t("ENUM", "Env")
SCHEMA: dict[str, Any] = {
    "queryType": {"name": "Query"},
    "mutationType": {"name": "Mutation"},
    "subscriptionType": None,
    "types": [
        {
            "kind": "OBJECT",
            "name": "Query",
            "fields": [
                _f("assets", _nn(_t("LIST", None, _nn(ASSET))), ("env", ENV), desc="Assets of an environment"),
                _f("asset", ASSET, ("id", _nn(ID))),
                _f("__typename", _nn(S)),
            ],
        },
        {
            "kind": "OBJECT",
            "name": "Mutation",
            "fields": [
                _f("tagAsset", ASSET, ("id", _nn(ID)), ("tag", _nn(S))),
                _f("deleteAsset", _t("SCALAR", "Boolean"), ("id", _nn(ID))),
                _f("getOrCreateTag", _t("OBJECT", "Tag"), ("name", _nn(S))),
            ],
        },
        {
            "kind": "OBJECT",
            "name": "Asset",
            "fields": [
                _f("id", _nn(ID)),
                _f("name", _nn(S)),
                _f("env", ENV),
                _f("owner", S),
                _f("site", _t("OBJECT", "Site")),
                _f("history", _t("LIST", None, _t("OBJECT", "Change")), ("since", _nn(S))),
            ],
        },
        {"kind": "OBJECT", "name": "Site", "fields": [_f("name", S), _f("rack", _t("OBJECT", "Rack"))]},
        {"kind": "OBJECT", "name": "Rack", "fields": [_f("label", S), _f("room", _t("OBJECT", "Room"))]},
        {"kind": "OBJECT", "name": "Room", "fields": [_f("code", S)]},
        {"kind": "OBJECT", "name": "Tag", "fields": [_f("name", S)]},
        {"kind": "OBJECT", "name": "Change", "fields": [_f("at", S)]},
        {"kind": "ENUM", "name": "Env", "enumValues": [{"name": "prod"}, {"name": "lab"}]},
    ],
}


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/graphql")

    @r.post("")
    async def graphql(request: Request) -> JSONResponse:
        body: dict[str, Any] = await request.json()
        if "__schema" in str(body.get("query") or ""):
            return JSONResponse({"data": {"__schema": SCHEMA}})
        auth = request.headers.get("authorization", "")
        kind = {"Bearer ro-graphql": "read", "Bearer rw-graphql": "write"}.get(auth)
        if kind is None:
            return JSONResponse({"errors": [{"message": "unauthenticated"}]}, status_code=401)
        query, variables = str(body.get("query") or ""), body.get("variables") or {}
        if "mutation" in query:
            if kind != "write":
                store.deny("graphql", "mutation", "read token")
                return JSONResponse({"errors": [{"message": "the read token cannot run mutations"}]})
            store.record("graphql", "graphql.tag", kind, {"id": variables.get("id"), "tag": variables.get("tag")})
            return JSONResponse({"data": {"tagAsset": {"id": variables.get("id"), "tags": [variables.get("tag")]}}})
        if "assets" in query:
            env = variables.get("env")
            return JSONResponse({"data": {"assets": [a for a in ASSETS if env in (None, a["env"])]}})
        return JSONResponse({"errors": [{"message": "unknown document"}]})

    return r
