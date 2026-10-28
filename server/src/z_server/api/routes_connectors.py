"""Connector catalog and exposure review (docs/05 §3). Credentials, import and tests come with the gateway."""

from __future__ import annotations

from sqlalchemy import func, select
from z_common.models import api_catalog as m
from z_common.models.enums import OperationClass
from z_common.packs.classify import ORDER

from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.auth.identity import now
from z_server.db.models import Agent, Connector, Credential, Operation
from z_server.errors import Problem

router = ContractRouter()


async def _connector(ctx: Ctx, ref: str) -> Connector:
    row = (
        await ctx.db.get(Connector, ref)
        or (await ctx.db.execute(select(Connector).where(Connector.name == ref))).scalar_one_or_none()
    )
    if row is None:
        raise Problem("not-found", f"no connector {ref!r}")
    return row


async def connector_out(ctx: Ctx, row: Connector) -> m.Connector:
    total = (
        await ctx.db.execute(select(func.count()).select_from(Operation).where(Operation.connector_id == row.id))
    ).scalar_one()
    exposed = (
        await ctx.db.execute(
            select(func.count())
            .select_from(Operation)
            .where(Operation.connector_id == row.id, Operation.exposed.is_(True))
        )
    ).scalar_one()
    used_by = sorted(
        a.handle
        for a in (await ctx.db.execute(select(Agent))).scalars()
        if any(t.startswith(f"gateway:{row.name}.") for t in a.profile.get("tools", []))
    )
    creds = (await ctx.db.execute(select(Credential).where(Credential.connector_id == row.id))).scalars()
    return m.Connector(
        id=row.id,
        name=row.name,
        kind=row.kind,
        display_name=row.display_name,
        base_url=row.base_url,
        data_class=row.data_class,
        review_status=row.review_status,
        enabled=row.enabled,
        health=m.ConnectorHealthInfo.model_validate(row.health or {"status": "disabled"}),
        operations_total=int(total),
        operations_exposed=int(exposed),
        used_by=used_by,
        credentials=[
            m.CredentialInfo(
                id=c.id,
                purpose=c.purpose,
                scope=c.scope,
                backend=c.backend,
                ref=c.ref,
                identity=c.identity,
                scopes=list(c.scopes),
                rotated_at=c.rotated_at,
                expires_at=c.expires_at,
            )
            for c in creds
        ],
        rate_limit=row.manifest.get("rate_limit"),
        pack=row.pack,
        manifest=row.manifest,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def operation_out(op: Operation, connector: str) -> m.Operation:
    return m.Operation.model_validate(
        {
            "id": op.id,
            "connector": connector,
            "source_ref": op.source_ref,
            "description": op.description,
            "class": op.op_class,
            "classifier_class": op.classifier_class,
            "exposed": op.exposed,
            "proposed_exposed": op.proposed_exposed,
            "rate_limit": op.rate_limit,
            "input_schema": op.input_schema,
            "reviewed_by": op.reviewed_by,
            "reviewed_at": op.reviewed_at,
        }
    )


@router.operation("listConnectors")
async def list_connectors(ctx: CtxDep) -> list[m.Connector]:
    return [
        await connector_out(ctx, c)
        for c in (await ctx.db.execute(select(Connector).order_by(Connector.name))).scalars()
    ]


@router.operation("getConnector")
async def get_connector(ctx: CtxDep, id: str) -> m.Connector:
    return await connector_out(ctx, await _connector(ctx, id))


@router.operation("listOperations")
async def list_operations(ctx: CtxDep, id: str) -> list[m.Operation]:
    row = await _connector(ctx, id)
    ops = (
        await ctx.db.execute(select(Operation).where(Operation.connector_id == row.id).order_by(Operation.id))
    ).scalars()
    return [operation_out(o, row.name) for o in ops]


@router.operation("approveExposure")
async def approve_exposure(ctx: CtxDep, id: str, body: m.ApproveExposure) -> m.Connector:
    row = await _connector(ctx, id)
    ops = {o.id: o for o in (await ctx.db.execute(select(Operation).where(Operation.connector_id == row.id))).scalars()}
    unknown = [d.id for d in body.operations if d.id not in ops]
    if unknown:
        raise Problem("validation", f"unknown operations: {', '.join(unknown)}")
    changes = []
    at = now()
    for decision in body.operations:
        op = ops[decision.id]
        if op.exposed != decision.exposed:
            changes.append({"operation": op.id, "class": op.op_class, "from": op.exposed, "to": decision.exposed})
        op.exposed, op.reviewed_by, op.reviewed_at = decision.exposed, ctx.user.username, at
    before = row.review_status
    row.review_status = "approved"
    if row.health.get("status") == "disabled" and row.health.get("detail") == "waiting for exposure review":
        row.health = {"status": "unreachable", "detail": "not checked yet"}
    await ctx.db.flush()
    await ctx.audit(
        "connector.exposure_approved",
        "connector",
        row.id,
        before={"review_status": before},
        after={"review_status": "approved", "changes": changes},
    )
    return await connector_out(ctx, row)


@router.operation("patchOperation")
async def patch_operation(ctx: CtxDep, id: str, body: m.OperationPatch) -> m.Operation:
    op = await ctx.db.get(Operation, id)
    if op is None:
        raise Problem("not-found")
    connector = await ctx.db.get(Connector, op.connector_id)
    before = operation_out(op, connector.name if connector else "?").dump()
    if body.class_ is not None:
        if ORDER[body.class_] < ORDER[OperationClass(op.classifier_class)]:
            raise Problem("validation", f"class cannot be lower than the platform floor {op.classifier_class!r}")
        op.op_class = body.class_.value
    if body.exposed is not None:
        op.exposed, op.reviewed_by, op.reviewed_at = body.exposed, ctx.user.username, now()
    if body.rate_limit is not None:
        op.rate_limit = body.rate_limit
    out = operation_out(op, connector.name if connector else "?")
    await ctx.audit("connector.operation_changed", "operation", op.id, before=before, after=out.dump())
    return out
