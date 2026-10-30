"""Every route is bound to a contract operation and declares its permission (deny by default), and every handler
takes the dependency that enforces it."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator
from typing import Any

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from z_server.api import framework, internal
from z_server.api.framework import contract_operations
from z_server.app import ROUTERS

OUTSIDE_CONTRACT = {"/healthz", "/readyz", "/metrics"}
# Internal handlers that check their token in the body instead of a dependency, and the call that does it:
# the job control long poll must not hold the job row lock the job context takes; the data class raise opens
# its own transaction to lock the mission.
INLINE_CHECK = {"internalJobControl": "_bearer(request)", "internalRaiseDataClass": "await require_service(request)"}


def test_every_api_route_is_a_contract_operation() -> None:
    ops = contract_operations()
    for router in ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute)
            op = ops[route.operation_id or ""]
            assert route.path == op.path, route.operation_id
            assert route.methods == {op.method}, route.operation_id
            assert op.permission, route.operation_id


def test_no_route_outside_the_contract_except_probes(client) -> None:  # type: ignore[no-untyped-def]
    paths = {r.path for r in client.app.routes if isinstance(r, APIRoute)}
    contract_paths = {op.path for op in contract_operations().values()}
    assert paths - contract_paths <= OUTSIDE_CONTRACT


def _calls(dependant: Dependant) -> Iterator[Callable[..., Any]]:
    for dep in dependant.dependencies:
        if dep.call is not None:
            yield dep.call
        yield from _calls(dep)


def test_every_handler_takes_the_context_that_enforces_its_permission() -> None:
    """x-permission is checked in the dependency (framework._ctx for API routes, the job token or service token
    for internal ones): a handler without it would answer anyone, whatever the contract declares."""
    ops = contract_operations()
    checked = 0
    for router in ROUTERS:
        for route in router.routes:
            assert isinstance(route, APIRoute)
            op = ops[route.operation_id or ""]
            calls = set(_calls(route.dependant))
            if op.operation_id in INLINE_CHECK:
                assert op.permission in {"job_token", "service"}, route.operation_id
                assert INLINE_CHECK[op.operation_id] in inspect.getsource(route.endpoint), route.operation_id
            elif op.permission == "job_token":
                assert internal._job_ctx in calls, route.operation_id
            elif op.permission == "service":
                assert internal.require_service in calls, route.operation_id
            else:
                assert framework._ctx in calls, route.operation_id
            checked += 1
    assert checked > 100  # the walk reached the routers


# POST routes that compute and change nothing (drafts, dry runs, connection tests, chain verification); every other
# public write is x-audit, and the router refuses to commit an x-audit request that wrote no audit event
# (framework._ctx raises), so "every mutating call has an audit event" holds by construction (invariant 9).
NOT_AUDITED_WRITES = {
    "draftPlan",
    "draftAgent",
    "testConnector",
    "importConnector",
    "adminDryRunGroupMapping",
    "adminTestAuthSettings",
    "adminVerifyAudit",
}


def test_every_public_write_is_audited() -> None:
    from z_server.api.framework import contract_operations

    unaudited = {
        o.operation_id
        for o in contract_operations().values()
        if o.method in {"POST", "PUT", "PATCH", "DELETE"}
        and o.permission not in {"job_token", "service"}
        and not o.audited
    }
    assert unaudited == NOT_AUDITED_WRITES
