"""A small GraphQL API (inventory of assets) for `graphql` connectors. It does not parse GraphQL: it answers the
two documents the tests declare. Read token `ro-graphql` may only query; write token `rw-graphql` may also run
the `tagAsset` mutation, recorded as a write of type `graphql.tag`."""

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


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/graphql")

    @r.post("")
    async def graphql(request: Request) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        kind = {"Bearer ro-graphql": "read", "Bearer rw-graphql": "write"}.get(auth)
        if kind is None:
            return JSONResponse({"errors": [{"message": "unauthenticated"}]}, status_code=401)
        body: dict[str, Any] = await request.json()
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
