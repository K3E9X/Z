"""Fake generic CMDB (`/cmdb/assets...`), bearer auth, read only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .state import Store


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/cmdb", dependencies=[Depends(store.dep("cmdb", "bearer", "read"))])

    def assets() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = store.data["cmdb"]["assets"]
        return result

    @r.get("/assets")
    def search(q: str | None = None, ip: str | None = None) -> dict[str, Any]:
        items = assets()
        if q:
            needle = q.lower()
            items = [
                a
                for a in items
                if any(needle in str(a.get(k, "")).lower() for k in ("name", "fqdn", "owner_team", "environment"))
            ]
        if ip:
            items = [a for a in items if a["ip"] == ip]
        return {"count": len(items), "results": items}

    @r.get("/assets/{name}")
    def get(name: str) -> dict[str, Any]:
        for asset in assets():
            if name in (asset["name"], asset.get("fqdn")):
                return asset
        raise HTTPException(404, detail=f"asset {name!r} not found")

    return r
