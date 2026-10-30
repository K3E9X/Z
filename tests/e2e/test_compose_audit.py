"""The audit trail of the compose e2e run (invariant 9, docs/12 §4). This module runs last (conftest): every
mutating z-server call that the suite made and that succeeded has an audit event with its request id, a denial is
audited, `z admin audit verify` passes, and rewriting one row as the database owner breaks the chain at that row."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.e2e import stack

pytestmark = pytest.mark.e2e

# public writes that change nothing and so write no audit event (server/tests/test_contract_routes.py)
UNAUDITED = {("POST", "/api/v1/admin/audit:verify"), ("POST", "/api/v1/missions/draft-plan")}


def audit_log(api: httpx.Client) -> list[dict[str, Any]]:
    export = api.get("/audit/export", params={"format": "json"})
    assert export.status_code == 200, export.text
    return [json.loads(line) for line in export.text.splitlines() if line.strip()]


def test_every_mutating_call_of_the_run_is_audited(api: httpx.Client) -> None:
    # a few calls of this test's own, so it checks something when it runs alone
    created = api.post(
        "/missions", json={"title": "Audit probe", "prompt": "Audit probe", "agents": ["@summarizer"], "start": False}
    )
    assert created.status_code == 201, created.text
    cancelled = api.post(f"/missions/{created.json()['id']}/cancel", json={"reason": "audit probe"})
    assert cancelled.status_code == 200, cancelled.text
    operator = stack.sign_in_local("tester")
    denied = operator.post("/connectors/jira/approve-exposure", json={"operations": []})
    assert denied.status_code == 403, denied.text

    events = audit_log(api)
    by_request: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_request.setdefault(event.get("request_id") or "", []).append(event)
    succeeded = [c for c in stack.RECORDED if c.status < 400 and (c.method, c.path) not in UNAUDITED]
    assert len(succeeded) >= 3, stack.RECORDED
    missing = [c for c in succeeded if c.request_id not in by_request]
    assert not missing, missing
    [refusal] = by_request[denied.headers["X-Request-Id"]]
    assert refusal["action"] == "authz.denied" and refusal["result"] == "denied"
    assert refusal["actor"] == "local:tester" and refusal["after"]["permission"] == "connectors:manage"


@pytest.fixture
def owner_rewrite() -> Iterator[Any]:
    """Rewrites the actor of one audit row as the database owner (the app role cannot), and puts it back."""
    changed: dict[int, str] = {}

    def rewrite(seq: int, actor: str) -> str:
        before = stack.psql_owner(f"SELECT actor FROM audit_events WHERE seq = {seq}")
        stack.psql_owner(
            "ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only; "
            f"UPDATE audit_events SET actor = '{actor}' WHERE seq = {seq}; "
            "ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only;"
        )
        changed.setdefault(seq, before)
        return before

    yield rewrite
    for seq, actor in changed.items():
        rewrite(seq, actor)


def test_audit_chain_verifies_and_a_rewritten_row_breaks_it(
    api: httpx.Client, owner_rewrite: Any, tmp_path: Path
) -> None:
    z = stack.z_cli(str(tmp_path))
    ok = z("admin", "audit", "verify", "--json")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    result = json.loads(ok.stdout)
    assert result["ok"] is True and result["checked"] >= 3, result

    created = api.post(
        "/missions", json={"title": "Tamper probe", "prompt": "Tamper probe", "agents": ["@summarizer"], "start": False}
    )
    assert created.status_code == 201, created.text
    [event] = api.get("/audit", params={"action": "mission.created", "target_id": created.json()["id"]}).json()["items"]
    original = owner_rewrite(event["seq"], "someone-else")
    assert original == "dev"

    broken = api.post("/admin/audit:verify", json={}).json()
    assert broken["ok"] is False and broken["first_bad_seq"] == event["seq"], broken
    cli = z("admin", "audit", "verify")
    assert cli.returncode != 0 and f"BROKEN at seq {event['seq']}" in " ".join((cli.stdout + cli.stderr).split()), (
        cli.stdout + cli.stderr
    )

    owner_rewrite(event["seq"], original)  # put back: the chain verifies again
    again = api.post("/admin/audit:verify", json={}).json()
    assert again["ok"] is True and again["checked"] >= event["seq"], again
