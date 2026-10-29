"""Connector administration (docs/05 §3, §4, §5, §6): operations sync, credential references, specs, probes.

Operation sync keeps the admin's review: an operation whose class is unchanged keeps its exposure; an operation
whose class rose, or a new one, starts unexposed until reviewed; removed operations disappear. The effective
class never drops below what an admin set (rules and patches only raise).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.connectors.graphql_import import INTROSPECTION_QUERY
from z_common.connectors.operations import OperationSpec, decide, from_openapi, materialize
from z_common.models.enums import OperationClass
from z_common.packs.classify import ORDER
from z_common.redaction import Redactor

from z_server.db.models import Connector, Credential, Operation
from z_server.errors import Problem
from z_server.gateway import mcp_client
from z_server.gateway.auth import AuthError, auth_config, material
from z_server.gateway.core import Gateway, shape
from z_server.ids import new_id
from z_server.security import check_outbound_url
from z_server.state import AppState

MAX_SPEC_BYTES = 5 * 1024 * 1024
BACKENDS = {"env", "file", "vault", "kubernetes"}
REVIEWED_SOURCES = {"browser": "flow", "graphql": "query", "database": "query"}


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def sync_operations(db: AsyncSession, connector: Connector, specs: list[OperationSpec]) -> SyncReport:
    report = SyncReport()
    existing = {
        o.id: o for o in (await db.execute(select(Operation).where(Operation.connector_id == connector.id))).scalars()
    }
    seen = set()
    for spec in specs:
        op_id = f"{connector.name}.{spec.local_id}"
        seen.add(op_id)
        row = existing.get(op_id)
        if row is None:
            db.add(
                Operation(
                    id=op_id,
                    connector_id=connector.id,
                    local_id=spec.local_id,
                    source_ref=spec.source_ref,
                    description=spec.description,
                    op_class=spec.op_class.value,
                    classifier_class=spec.classifier_class.value,
                    exposed=False,
                    proposed_exposed=spec.proposed_exposed,
                    input_schema=spec.input_schema,
                )
            )
            report.added.append(op_id)
            continue
        new_class = max(spec.op_class, OperationClass(row.op_class), key=lambda c: ORDER[c])
        if new_class.value != row.op_class and row.exposed:
            row.exposed = False
            report.warnings.append(f"{op_id}: class rose to {new_class.value}; exposure needs a new review")
        if connector.kind in REVIEWED_SOURCES and row.source_ref != spec.source_ref and row.exposed:
            row.exposed = False  # what the operation does changed (flow steps, query): an admin reviews it again
            report.warnings.append(
                f"{op_id}: its {REVIEWED_SOURCES[connector.kind]} changed; exposure needs a new review"
            )
        if (row.source_ref, row.input_schema, row.op_class) != (spec.source_ref, spec.input_schema, new_class.value):
            report.changed.append(op_id)
        row.source_ref, row.description, row.input_schema = spec.source_ref, spec.description, spec.input_schema
        row.classifier_class, row.op_class, row.proposed_exposed = (
            spec.classifier_class.value,
            new_class.value,
            spec.proposed_exposed,
        )
    gone = [op_id for op_id in existing if op_id not in seen]
    if gone:
        await db.execute(delete(Operation).where(Operation.id.in_(gone)))
        report.removed = sorted(gone)
    return report


async def sync_credentials(db: AsyncSession, connector: Connector) -> None:
    """Credential rows mirror the manifest's auth references (values never stored)."""
    wanted: dict[str, dict[str, Any]] = {}
    for purpose in ("read", "write"):
        auth = auth_config(connector.manifest, purpose)
        if auth and auth.get("secret"):
            ref = str(auth["secret"])
            wanted[purpose] = {"backend": ref.split(":", 1)[0], "ref": ref, "identity": auth.get("username")}
    rows = {
        c.purpose: c
        for c in (
            await db.execute(
                select(Credential).where(Credential.connector_id == connector.id, Credential.scope == "service")
            )
        ).scalars()
    }
    for purpose, data in wanted.items():
        row = rows.get(purpose)
        if row is None:
            db.add(Credential(id=new_id("cred"), connector_id=connector.id, purpose=purpose, scope="service", **data))
        elif (row.ref, row.identity) != (data["ref"], data["identity"]):
            row.backend, row.ref, row.identity = data["backend"], data["ref"], data["identity"]
            row.rotated_at = None
    for purpose, row in rows.items():
        if purpose not in wanted:
            await db.delete(row)


async def _fetch_capped(state: AppState, method: str, url: str, what: str, **kwargs: Any) -> tuple[int, bytes]:
    """(status, body) of an admin-entered URL, read with a byte cap (never buffered whole)."""
    await check_outbound_url(url, state)
    data = bytearray()
    try:
        async with state.http.stream(method, url, timeout=20, follow_redirects=False, **kwargs) as resp:
            async for chunk in resp.aiter_bytes():
                data += chunk
                if len(data) > MAX_SPEC_BYTES:
                    raise Problem("validation", f"the {what} is larger than 5 MB")
            return resp.status_code, bytes(data)
    except Problem:
        raise
    except Exception as exc:
        raise Problem("validation", f"could not fetch the {what}: {type(exc).__name__}") from exc


async def fetch_spec(state: AppState, url: str) -> dict[str, Any]:
    status, body = await _fetch_capped(state, "GET", url, "spec")
    if status != 200:
        raise Problem("validation", f"the spec URL answered {status}")
    return parse_spec(body.decode("utf-8", errors="replace"))


async def fetch_introspection(state: AppState, endpoint: str) -> dict[str, Any]:
    """Run the introspection query against a GraphQL endpoint, without credentials (like a spec URL)."""
    status, raw = await _fetch_capped(
        state,
        "POST",
        endpoint,
        "introspection result",
        json={"query": INTROSPECTION_QUERY},
        headers={"Accept": "application/json"},
    )
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    errors = body.get("errors") if isinstance(body, dict) else None
    if status != 200 or errors or not isinstance(body, dict):
        raise Problem(
            "validation",
            f"the endpoint refused introspection (HTTP {status}): run it with a read token and paste the "
            "result instead",
        )
    return body


def parse_spec(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        doc = json.loads(raw)
    except ValueError:
        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise Problem("validation", "the spec is neither JSON nor YAML") from exc
    if not isinstance(doc, dict) or not (doc.get("openapi") or doc.get("swagger")) or "paths" not in doc:
        raise Problem("validation", "not an OpenAPI document (openapi/swagger and paths required)")
    return doc


def operation_specs(manifest: dict[str, Any], spec_doc: dict[str, Any] | None) -> list[OperationSpec]:
    found = {s.local_id: s for s in materialize(manifest)}
    if spec_doc is not None:
        for s in from_openapi(manifest, spec_doc):
            found[s.local_id] = s
    return list(found.values())


async def mcp_specs(state: AppState, manifest: dict[str, Any]) -> tuple[dict[str, Any], list[OperationSpec], list[str]]:
    """Discover an MCP server's tools; unmapped tools get `tools.<name>` ids. Hints are used, never trusted alone."""
    warnings: list[str] = []
    headers: dict[str, str] = {}
    gateway: Gateway = state.gateway
    try:
        headers = (await material(manifest, "read", gateway.resolver)).headers
    except AuthError as exc:
        warnings.append(f"discovery without credentials: {exc}")
    tools = await mcp_client.list_tools(manifest, manifest.get("base_url"), headers, gateway.resolver)
    mapping = dict(manifest.get("operation_map") or {})
    specs = []
    for tool in tools:
        local = mapping.get(tool.name) or "tools." + "".join(
            ch if ch.isalnum() else "_" for ch in tool.name.lower()
        ).strip("_")
        mapping.setdefault(tool.name, local)
        hint = tool.annotations.readOnlyHint if tool.annotations else None
        floor, cls, exposed = decide({**manifest, "operation_map": mapping}, local, None, hint)
        specs.append(
            OperationSpec(
                local,
                f"mcp:{tool.name}",
                floor,
                cls,
                exposed,
                tool.inputSchema or {"type": "object"},
                tool.description or "",
            )
        )
    return {**manifest, "operation_map": mapping}, specs, warnings


async def probe(
    state: AppState, connector: Connector, operation: Operation | None = None, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A cheap read with the read credential (never a write). Returns ConnectorTestResult fields."""
    gateway: Gateway = state.gateway
    started = time.monotonic()
    health = connector.manifest.get("health") or {}
    if operation is None and health.get("operation"):  # the probe the manifest declares (health.operation)
        async with state.sessions() as db:
            operation = await db.get(Operation, f"{connector.name}.{health['operation']}")
        if operation is None or operation.connector_id != connector.id:
            return {
                "ok": False,
                "latency_ms": 0,
                "status_code": None,
                "detail": f"health.operation {health['operation']} is not an operation of the connector",
                "sample": None,
            }
    if operation is not None:
        if operation.op_class != OperationClass.read.value:
            raise Problem("validation", "only read operations can be used to test a connector")
        inv = await gateway.invoke(connector, operation, params or health.get("params") or {}, "read", timeout_s=15)
    elif connector.kind in {"rest", "openapi"} and health.get("path"):
        inv = await gateway.send(connector, "GET", str(health["path"]), "read", timeout_s=15)
    elif connector.kind == "mcp":
        try:
            _, specs, warnings = await mcp_specs(state, connector.manifest)
            return {
                "ok": True,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "status_code": None,
                "detail": f"{len(specs)} tools listed" + (f"; {warnings[0]}" if warnings else ""),
                "sample": [s.local_id for s in specs][:20],
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "status_code": None,
                "detail": str(exc)[:300],
                "sample": None,
            }
    else:
        return {
            "ok": False,
            "latency_ms": 0,
            "status_code": None,
            "detail": "no health probe: set health.path or health.operation, or test an operation",
            "sample": None,
        }
    sample = Redactor(inv.secrets).value(shape(inv.body))
    text = json.dumps(sample, default=str)
    return {
        "ok": inv.ok,
        "latency_ms": inv.latency_ms,
        "status_code": inv.status,
        "detail": "ok" if inv.ok else (inv.error or "failed"),
        "sample": sample if len(text) <= 4000 else text[:4000],
    }
