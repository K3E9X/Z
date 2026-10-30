"""History and audit: list, export, verify (docs/12 §4)."""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, select
from z_common.models import api_identity as m

from z_server import audit
from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.api.views import audit_out
from z_server.db.models import AuditEvent, Setting
from z_server.errors import Problem
from z_server.ids import new_id

router = ContractRouter()


def _filtered(
    ctx: Ctx,
    actor: str | None,
    action: str | None,
    target_type: str | None,
    target_id: str | None,
    result: str | None,
    ip: str | None,
    frm: datetime | None,
    to: datetime | None,
) -> Select[Any]:
    stmt = select(AuditEvent)
    if not ctx.user.can("audit:read"):
        stmt = stmt.where(AuditEvent.actor == ctx.user.username)  # audit:read_own
    for column, value in (
        (AuditEvent.actor, actor),
        (AuditEvent.target_type, target_type),
        (AuditEvent.target_id, target_id),
        (AuditEvent.result, result),
        (AuditEvent.actor_ip, ip),
    ):
        if value:
            stmt = stmt.where(column == value)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if frm:
        stmt = stmt.where(AuditEvent.ts >= frm)
    if to:
        stmt = stmt.where(AuditEvent.ts < to)
    return stmt


@router.operation("listAudit")
async def list_audit(
    ctx: CtxDep,
    cursor: str | None = None,
    limit: int = 50,
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    result: str | None = None,
    ip: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
) -> dict[str, Any]:
    stmt = _filtered(ctx, actor, action, target_type, target_id, result, ip, from_, to)
    if cursor:
        if not cursor.isdigit():
            raise Problem("validation", "bad cursor")
        stmt = stmt.where(AuditEvent.seq < int(cursor))
    limit = max(1, min(limit, 200))
    rows = list((await ctx.db.execute(stmt.order_by(AuditEvent.seq.desc()).limit(limit + 1))).scalars())
    nxt = str(rows[limit - 1].seq) if len(rows) > limit else None
    return {"items": [audit_out(r).dump() for r in rows[:limit]], "next_cursor": nxt}


@router.operation("exportAudit")
async def export_audit(
    ctx: CtxDep,
    format: str = "json",
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
) -> StreamingResponse:
    if format not in {"json", "csv", "syslog"}:
        raise Problem("validation", "format must be json, csv or syslog")
    await ctx.audit("audit.exported", "audit_log", None, after={"format": format, "from": from_, "to": to})
    stmt = _filtered(ctx, None, None, None, None, None, None, from_, to).order_by(AuditEvent.seq)
    rows = list((await ctx.db.execute(stmt)).scalars())  # read inside the request transaction

    async def body() -> AsyncIterator[str]:
        if format == "csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow([*audit.FIELDS, "prev_hash", "hash"])
            for row in rows:
                line = audit.export_line(row)
                writer.writerow(
                    [
                        audit.canonical_json(line[f]) if isinstance(line[f], dict | list) else line[f]
                        for f in [*audit.FIELDS, "prev_hash", "hash"]
                    ]
                )
            yield buffer.getvalue()
            return
        for row in rows:
            yield (
                audit.syslog_line(row) if format == "syslog" else audit.canonical_json(audit.export_line(row))
            ) + "\n"

    media = {"json": "application/x-ndjson", "csv": "text/csv", "syslog": "text/plain"}[format]
    extension = {"json": "jsonl", "csv": "csv", "syslog": "log"}[format]
    return StreamingResponse(
        body(), media_type=media, headers={"Content-Disposition": f'attachment; filename="z-audit.{extension}"'}
    )


@router.operation("adminVerifyAudit")
async def admin_verify_audit(ctx: CtxDep, body: dict[str, Any] | None = None) -> m.AuditVerify:
    result = await audit.verify(ctx.db, ctx.state.signer)
    return m.AuditVerify(
        ok=result.ok,
        checked=result.checked,
        first_bad_seq=result.first_bad_seq,
        checkpoints_verified=result.checkpoints_verified,
        detail=result.detail,
    )


# ---------------------------------------------------------------- saved searches (per user, in `settings`)

SEARCHES_PREFIX = "audit_searches:"
MAX_SEARCHES = 50


class AuditSearchFilters(BaseModel):
    """contracts AuditSearch.filters: the listAudit query parameters."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    actor: str | None = None
    action: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    result: str | None = None
    ip: str | None = None
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None


class AuditSearchWrite(BaseModel):
    """contracts AuditSearchWrite."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    filters: AuditSearchFilters


async def _searches(ctx: Ctx, *, lock: bool = False) -> tuple[Setting | None, list[dict[str, Any]]]:
    row = await ctx.db.get(Setting, SEARCHES_PREFIX + ctx.user.user_id, with_for_update=lock)
    return row, list((row.value or {}).get("items") or []) if row else []


async def _save_searches(ctx: Ctx, row: Setting | None, items: list[dict[str, Any]]) -> None:
    value = {"items": items}
    if row is None:
        ctx.db.add(Setting(key=SEARCHES_PREFIX + ctx.user.user_id, value=value, updated_by=ctx.user.username))
    else:
        row.value, row.updated_by = value, ctx.user.username
    await ctx.db.flush()


@router.operation("listAuditSearches")
async def list_audit_searches(ctx: CtxDep) -> list[dict[str, Any]]:
    _, items = await _searches(ctx)
    return items


@router.operation("createAuditSearch", status_code=201)
async def create_audit_search(ctx: CtxDep, body: AuditSearchWrite) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise Problem("validation", "the name cannot be blank")
    row, items = await _searches(ctx, lock=True)
    if any(str(i.get("name", "")).casefold() == name.casefold() for i in items):
        raise Problem("conflict", f"you already have a saved search named {name!r}")
    if len(items) >= MAX_SEARCHES:
        raise Problem("conflict", f"at most {MAX_SEARCHES} saved searches: delete one first")
    search_id = new_id("aus")
    item: dict[str, Any] = {
        "id": search_id,
        "name": name,
        "filters": body.filters.model_dump(mode="json", by_alias=True, exclude_none=True),
        "created_at": datetime.now(UTC).isoformat(),
    }
    await _save_searches(ctx, row, [*items, item])
    await ctx.audit("audit.search_saved", "audit_search", search_id, after={"name": name, "filters": item["filters"]})
    return item


@router.operation("deleteAuditSearch", status_code=204)
async def delete_audit_search(ctx: CtxDep, id: str) -> Response:
    row, items = await _searches(ctx, lock=True)
    gone = next((i for i in items if i.get("id") == id), None)
    if gone is None:
        raise Problem("not-found", "no such saved search")
    await _save_searches(ctx, row, [i for i in items if i.get("id") != id])
    await ctx.audit("audit.search_deleted", "audit_search", id, before={"name": gone.get("name")})
    return Response(status_code=204)
