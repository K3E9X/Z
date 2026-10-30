"""GraphQL and database operations (docs/05 §1): the reviewed document of a declared operation, never free text
from the agent; the agent only supplies its variables or parameters.

- graphql: POST {query, variables} to the endpoint with the injected credential; a read operation whose document
  may contain a mutation or a subscription is refused here too (the connector validation already refuses it).
- database (PostgreSQL): the declared SELECT runs in a READ ONLY transaction with a statement timeout and a row
  cap, as the connector's read account; database connectors have no writes.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import psycopg
from psycopg.rows import dict_row
from z_common.connectors.queries import QueryError, check_select, graphql_writes
from z_common.models.enums import OperationClass

from z_server.db.models import Connector, Operation
from z_server.gateway import http as http_invoker
from z_server.gateway.auth import AuthMaterial, Purpose

MAX_ROWS = 1000
STATEMENT_TIMEOUT_MS = 10_000


class QueryRefused(Exception):
    pass


async def graphql(
    client: httpx.AsyncClient,
    connector: Connector,
    op: Operation,
    args: dict[str, Any],
    auth: AuthMaterial,
    timeout_s: float,
) -> tuple[bool, int | None, Any, str | None]:
    document = op.source_ref
    if op.op_class == OperationClass.read.value and graphql_writes(document):
        raise QueryRefused(f"{op.id} is a read operation but its document contains a mutation or subscription")
    endpoint = http_invoker.base_url(connector.base_url, connector.manifest)
    try:
        resp = await client.post(
            endpoint,
            json={"query": document, "variables": args},
            headers={**auth.headers, "Content-Type": "application/json", "Accept": "application/json"},
            timeout=timeout_s,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        return False, None, None, f"the GraphQL endpoint is unreachable ({type(exc).__name__})"
    try:
        body = resp.json()
    except ValueError:
        return False, resp.status_code, resp.text[:2000], f"HTTP {resp.status_code}"
    errors = body.get("errors") if isinstance(body, dict) else None
    if resp.status_code != 200 or errors:
        first = (errors or [{}])[0] if isinstance(errors, list) else {}
        message = first.get("message") if isinstance(first, dict) else None
        return False, resp.status_code, body, str(message or f"HTTP {resp.status_code}")[:500]
    return True, resp.status_code, body.get("data") if isinstance(body, dict) else body, None


def dsn(connector: Connector, auth: AuthMaterial) -> str:
    base = http_invoker.base_url(connector.base_url, connector.manifest)
    parsed = urlparse(base)
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
        raise QueryRefused("database connectors support postgresql://host[:port]/database base URLs")
    user = auth.login.get("username") or ""
    password = auth.login.get("password") or ""
    if not user:
        raise QueryRefused("the database credential is a basic credential (username and password)")
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{parsed.hostname}" + (
        f":{parsed.port}" if parsed.port else ""
    )
    return f"postgresql://{netloc}{parsed.path}" + (f"?{parsed.query}" if parsed.query else "")


async def database(
    connector: Connector, op: Operation, args: dict[str, Any], auth: AuthMaterial, purpose: Purpose
) -> tuple[bool, Any, str | None, int]:
    if purpose != "read" or op.op_class != OperationClass.read.value:
        raise QueryRefused("database connectors are read-only")
    try:
        params = check_select(op.source_ref)
    except QueryError as exc:
        raise QueryRefused(str(exc)) from exc
    values = {name: args.get(name) for name in params}
    started = time.monotonic()
    try:
        async with await psycopg.AsyncConnection.connect(
            dsn(connector, auth), connect_timeout=10, autocommit=False
        ) as conn:
            await conn.set_read_only(True)
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
                await cur.execute(op.source_ref, values)  # the reviewed query; the agent only binds parameters
                rows = await cur.fetchmany(MAX_ROWS + 1)
            await conn.rollback()
    except psycopg.Error as exc:
        message = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:300]
        return False, None, f"query failed: {message}", int((time.monotonic() - started) * 1000)
    truncated = len(rows) > MAX_ROWS
    out = {"rows": rows[:MAX_ROWS], "row_count": min(len(rows), MAX_ROWS), "truncated": truncated}
    return True, out, None, int((time.monotonic() - started) * 1000)
