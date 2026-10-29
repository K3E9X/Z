"""Connector catalog and exposure review (docs/05 §3). Credentials, import and tests come with the gateway."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from z_common.contracts import validator as contract_validator
from z_common.models import api_catalog as m
from z_common.models.enums import OperationClass
from z_common.packs.classify import ORDER

from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.auth.identity import now
from z_server.db.models import Agent, Connector, Credential, Operation
from z_server.errors import Problem
from z_server.gateway import admin
from z_server.ids import new_id
from z_server.security import check_outbound_url

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
    creds = (  # personal (user-scope) grants belong to their owner: listed only in /me/connections
        await ctx.db.execute(select(Credential).where(Credential.connector_id == row.id, Credential.scope != "user"))
    ).scalars()
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


# ---------------------------------------------------------------- create, edit, test, import (B3)


CONNECTOR_NAME = re.compile(r"^[a-z][a-z0-9]{1,62}$")  # Slug and the OperationId prefix: their only common charset
SECRET_FIELDS = ("secret", "cert", "key")


def _secret_refs(manifest: dict[str, Any]) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for purpose, auth in (manifest.get("auth") or {}).items():
        if isinstance(auth, dict):
            for f in SECRET_FIELDS:
                if f in auth:
                    out.append((f"auth.{purpose}.{f}", auth[f]))
    for app in ("read_app", "write_app"):
        cfg = (manifest.get("oauth") or {}).get(app)
        if isinstance(cfg, dict) and "secret" in cfg:
            out.append((f"oauth.{app}.secret", cfg["secret"]))
    for name, value in ((manifest.get("mcp") or {}).get("env") or {}).items():
        out.append((f"mcp.env.{name}", value))
    return out


async def _validated_manifest(ctx: Ctx, manifest: dict[str, Any]) -> dict[str, Any]:
    """Credentials are checked first, and schema errors never echo values: a pasted token must not come back
    in the response or the logs."""
    name = manifest.get("name")
    if not isinstance(name, str) or not CONNECTOR_NAME.match(name):
        raise Problem("validation", "connector names use lowercase letters and digits only (they prefix operation ids)")
    for where, value in _secret_refs(manifest):
        if where.startswith("mcp.env") and isinstance(value, str) and ":" not in value:
            continue  # plain configuration values are allowed in mcp.env
        if not isinstance(value, str) or value.split(":", 1)[0] not in admin.BACKENDS or ":" not in value:
            raise Problem(
                "validation", f"{where} must be a secret reference (env:, file:, vault:, kubernetes:), never a value"
            )
    read, write = ((manifest.get("auth") or {}).get(p) or {} for p in ("read", "write"))
    if (
        isinstance(read, dict)
        and isinstance(write, dict)
        and read.get("secret")
        and read.get("secret") == write.get("secret")
    ):
        raise Problem("validation", "read and write credentials must be different secrets (invariant 7)")
    apps = manifest.get("oauth") or {}
    read_app, write_app = (apps.get(a) or {} for a in ("read_app", "write_app"))
    if (
        read_app
        and write_app
        and (
            read_app.get("client_id") == write_app.get("client_id")
            or (read_app.get("secret") and read_app.get("secret") == write_app.get("secret"))
        )
    ):
        raise Problem("validation", "the read and write OAuth apps must be different registrations (invariant 7)")
    for purpose in ("read", "write"):
        auth = (manifest.get("auth") or {}).get(purpose)
        for scope_flow in (auth or {}).values() if isinstance(auth, dict) and "type" not in auth else []:
            wanted = "read_app" if purpose == "read" else "write_app"
            if isinstance(scope_flow, dict) and scope_flow.get("app") not in (None, wanted):
                raise Problem("validation", f"auth.{purpose} must use the {wanted} (read and write stay apart)")
    problems = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: does not match the connector schema ({e.validator})"
        for e in contract_validator("connector.schema.json").iter_errors(manifest)
    ]
    if problems:
        raise Problem("validation", "; ".join(sorted(set(problems))[:5]))
    if manifest.get("base_url") and "${" not in str(manifest["base_url"]):
        await check_outbound_url(str(manifest["base_url"]), ctx.state)
    return manifest


@router.operation("createConnector", status_code=201)
async def create_connector(ctx: CtxDep, body: dict[str, Any]) -> m.Connector:
    manifest = await _validated_manifest(ctx, body)
    if (await ctx.db.execute(select(Connector).where(Connector.name == manifest["name"]))).scalar_one_or_none():
        raise Problem("conflict", f"connector {manifest['name']!r} already exists")
    spec_doc = None
    spec = manifest.get("spec")
    if manifest["kind"] == "openapi" and isinstance(spec, str) and spec.startswith("http"):
        spec_doc = await admin.fetch_spec(ctx.state, spec)
    row = Connector(
        id=new_id("con"),
        name=manifest["name"],
        kind=manifest["kind"],
        display_name=manifest.get("display_name") or manifest["name"],
        base_url=manifest.get("base_url"),
        data_class=manifest.get("data_class", "C2"),
        review_status="pending_review",
        enabled=True,
        manifest=manifest,
        health={"status": "disabled", "detail": "waiting for exposure review"},
    )
    ctx.db.add(row)
    await ctx.db.flush()
    report = await admin.sync_operations(ctx.db, row, admin.operation_specs(manifest, spec_doc))
    await admin.sync_credentials(ctx.db, row)
    await ctx.db.flush()
    await ctx.audit(
        "connector.created",
        "connector",
        row.id,
        after={"name": row.name, "kind": row.kind, "operations": len(report.added), "data_class": row.data_class},
    )
    return await connector_out(ctx, row)


@router.operation("patchConnector")
async def patch_connector(ctx: CtxDep, id: str, body: m.ConnectorPatch) -> m.Connector:
    row = await _connector(ctx, id)
    before = {"enabled": row.enabled, "review_status": row.review_status}
    after: dict[str, Any] = {}
    if body.manifest is not None:
        manifest = await _validated_manifest(ctx, body.manifest.dump())
        if manifest["name"] != row.name or manifest["kind"] != row.kind:
            raise Problem("validation", "name and kind cannot change; create a new connector")
        spec = manifest.get("spec")
        spec_doc = (
            await admin.fetch_spec(ctx.state, spec)
            if manifest["kind"] == "openapi" and isinstance(spec, str) and spec.startswith("http")
            else None
        )
        row.manifest, row.base_url = manifest, manifest.get("base_url")
        row.display_name = manifest.get("display_name") or row.name
        if manifest.get("data_class") and manifest["data_class"] != row.data_class:
            after["data_class"] = manifest["data_class"]
            row.data_class = manifest["data_class"]
        report = await admin.sync_operations(ctx.db, row, admin.operation_specs(manifest, spec_doc))
        await admin.sync_credentials(ctx.db, row)
        if report.added or report.warnings:
            row.review_status = "pending_review"  # new or raised operations need a review
        after.update(
            {"added": report.added, "changed": report.changed, "removed": report.removed, "warnings": report.warnings}
        )
        if ctx.state.gateway is not None:
            ctx.state.gateway.resolver.forget()
    if body.enabled is not None:
        row.enabled = body.enabled
        after["enabled"] = body.enabled
    await ctx.db.flush()
    await ctx.audit("connector.changed", "connector", row.id, before=before, after=after)
    return await connector_out(ctx, row)


@router.operation("testConnector")
async def test_connector(ctx: CtxDep, id: str, body: m.ConnectorTestRequest) -> m.ConnectorTestResult:
    row = await _connector(ctx, id)
    op = None
    if body.operation:
        op_id = body.operation if body.operation.startswith(f"{row.name}.") else f"{row.name}.{body.operation}"
        op = await ctx.db.get(Operation, op_id)
        if op is None or op.connector_id != row.id:
            raise Problem("validation", f"unknown operation {body.operation}")
    result = await admin.probe(ctx.state, row, op, body.params)
    stats = ctx.state.gateway.stats.summary(row.name) if ctx.state.gateway else {}
    row.health = {
        "status": "healthy" if result["ok"] else "unreachable",
        "latency_p50_ms": stats.get("latency_p50_ms"),
        "latency_p95_ms": stats.get("latency_p95_ms") or result["latency_ms"],
        "error_rate": stats.get("error_rate"),
        "last_success_at": now().isoformat() if result["ok"] else (row.health or {}).get("last_success_at"),
        "checked_at": now().isoformat(),
        "detail": result["detail"][:300],
    }
    await ctx.audit(
        "connector.tested",
        "connector",
        row.id,
        result="success" if result["ok"] else "error",
        after={"operation": body.operation, "status": result["status_code"], "latency_ms": result["latency_ms"]},
    )
    return m.ConnectorTestResult.model_validate(result)


@router.operation("importConnector")
async def import_connector(ctx: CtxDep, body: m.ConnectorImport) -> m.ConnectorDraft:
    """Draft a connector from a spec or an MCP server; nothing is saved (create it with POST /connectors)."""
    manifest: dict[str, Any] = {
        "name": body.name,
        "display_name": body.display_name or body.name,
        "data_class": (body.data_class.value if body.data_class else "C2"),
    }
    if body.base_url:
        manifest["base_url"] = body.base_url
    warnings: list[str] = []
    if body.source == "openapi":
        if body.spec is not None:
            doc = admin.parse_spec(body.spec)
        elif body.spec_url:
            doc = await admin.fetch_spec(ctx.state, body.spec_url)
            manifest["spec"] = body.spec_url
        else:
            raise Problem("validation", "openapi import needs spec or spec_url")
        manifest.update({"kind": "openapi", "operation_ids": "from_operation_id"})
        if not body.base_url:
            servers = doc.get("servers") or []
            if servers and isinstance(servers[0], dict) and str(servers[0].get("url", "")).startswith("http"):
                manifest["base_url"] = servers[0]["url"]
            else:
                warnings.append("no base_url: set it before creating the connector")
        manifest["auth"] = {
            "read": {"type": "bearer", "secret": f"env:{body.name.upper().replace('-', '_')}_TOKEN_RO"},
            "write": {"type": "bearer", "secret": f"env:{body.name.upper().replace('-', '_')}_TOKEN_RW"},
        }
        specs = admin.operation_specs(manifest, doc)
        if body.spec is not None:
            warnings.append(
                "the spec was pasted: keep it in packs/<pack>/connectors/ and reference it, or host it, so updates can be re-imported"
            )
    elif body.source == "mcp":
        if body.mcp is None:
            raise Problem("validation", "mcp import needs the mcp server (transport and command or url)")
        prefix = body.name.upper()
        manifest.update(
            {
                "kind": "mcp",
                "mcp": body.mcp.dump(),
                "auth": {
                    "read": {"type": "bearer", "secret": f"env:{prefix}_TOKEN_RO"},
                    "write": {"type": "bearer", "secret": f"env:{prefix}_TOKEN_RW"},
                },
            }
        )
        try:
            manifest, specs, more = await admin.mcp_specs(ctx.state, manifest)
        except Exception as exc:
            raise Problem("validation", str(exc)) from exc
        warnings += more
    else:
        raise Problem("unavailable", f"import from {body.source} is not available on this server yet")
    manifest["operations"] = {
        "default_exposed": False,
        "rules": [
            {"match": s.local_id, "class": s.op_class.value, "exposed": s.op_class == OperationClass.read}
            for s in specs
        ],
    }
    ops = [
        m.Operation.model_validate(
            {
                "id": f"{body.name}.{s.local_id}",
                "connector": body.name,
                "source_ref": s.source_ref,
                "description": s.description,
                "class": s.op_class.value,
                "classifier_class": s.classifier_class.value,
                "exposed": False,
                "proposed_exposed": s.op_class == OperationClass.read,
                "input_schema": s.input_schema,
            }
        )
        for s in specs
    ]
    destructive = [o.id for o in ops if o.class_ == OperationClass.destructive]
    if destructive:
        warnings.append(f"{len(destructive)} destructive operations stay unexposed: {', '.join(destructive[:5])}")
    await ctx.audit(
        "connector.import_drafted",
        "connector",
        None,
        after={"name": body.name, "source": body.source, "operations": len(ops)},
    )
    return m.ConnectorDraft.model_validate(
        {"manifest": manifest, "operations": [o.dump() for o in ops], "warnings": warnings}
    )


@router.operation("recordBrowserFlow", status_code=201)
async def record_browser_flow(ctx: CtxDep, body: dict[str, Any]) -> m.Connector:
    """Browser connectors are recorded and replayed by the z-browser worker, which is not part of this build:
    say so instead of pretending (docs/05 browser connectors)."""
    raise Problem(
        "unavailable",
        "recording a browser flow needs the z-browser worker, which is not deployed yet; "
        "use an OpenAPI, REST or MCP connector for this application",
    )
