"""Every route is bound to a contract operation and declares its permission (deny by default)."""

from __future__ import annotations

from fastapi.routing import APIRoute
from z_server.api.framework import contract_operations
from z_server.app import ROUTERS

OUTSIDE_CONTRACT = {"/healthz", "/readyz", "/metrics"}


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
