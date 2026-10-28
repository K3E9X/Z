"""Fake Logos implementing packs/ticketing/connectors/logos.openapi.json, bearer auth."""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .state import Store, TokenKind

SPEC_PATH = Path(
    os.environ.get(
        "FAKE_LOGOS_SPEC",
        Path(__file__).resolve().parents[3] / "packs" / "ticketing" / "connectors" / "logos.openapi.json",
    )
)


def load_spec(server_url: str) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    spec["servers"] = [{"url": server_url}]
    return spec


def _age_days(iso: str | None) -> float:
    if iso is None:
        return float("inf")
    return (time.time() - datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) / 86400


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/logos")
    read = store.dep("logos", "bearer", "read")
    write = store.dep("logos", "bearer", "write")

    def data() -> dict[str, Any]:
        result: dict[str, Any] = store.data["logos"]
        return result

    def account(aid: str) -> dict[str, Any]:
        for acc in data()["accounts"]:
            if acc["id"] == aid:
                return acc
        raise HTTPException(404, detail=f"account {aid!r} not found")

    @r.get("/openapi.json")
    def spec(request: Request) -> dict[str, Any]:
        return load_spec(str(request.base_url).rstrip("/") + "/logos")

    @r.get("/assets/{aid}", dependencies=[Depends(read)])
    def asset(aid: str) -> dict[str, Any]:
        for item in data()["assets"]:
            if aid in (item["id"], item["hostname"]):
                return item
        raise HTTPException(404, detail=f"asset {aid!r} not found")

    @r.get("/accounts", dependencies=[Depends(read)])
    def accounts(inactive_days: int | None = None) -> list[dict[str, Any]]:
        items = data()["accounts"]
        if inactive_days is not None:
            items = [a for a in items if _age_days(a["last_login"]) >= inactive_days]
        return [
            {**a, "inactive_days": None if a["last_login"] is None else int(_age_days(a["last_login"]))} for a in items
        ]

    @r.post("/accounts/{aid}/disable")
    def disable(aid: str, kind: Annotated[TokenKind, Depends(write)]) -> dict[str, Any]:
        with store.lock:
            acc = account(aid)
            already = acc["status"] == "disabled"
            acc["status"] = "disabled"
        store.record(
            "logos",
            "logos.accounts.disable",
            kind,
            {"id": aid, "username": acc["username"], "already_disabled": already},
        )
        return acc

    @r.delete("/accounts/{aid}", status_code=204)
    def delete(aid: str, kind: Annotated[TokenKind, Depends(write)]) -> Response:
        with store.lock:
            acc = account(aid)
            data()["accounts"].remove(acc)
        store.record("logos", "logos.accounts.delete", kind, {"id": aid, "username": acc["username"]})
        return Response(status_code=204)

    return r
