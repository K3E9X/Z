"""`/metrics` (docs/06 §5): Z values from the database next to the process metrics."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from z_server import usage
from z_server.db.models import Mission

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.server import csrf, seed_packs, sign_in_as


def scrape(client: TestClient) -> dict[str, dict[tuple[tuple[str, str], ...], float]]:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    out: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}
    for family in text_string_to_metric_families(resp.text):
        for sample in family.samples:
            out.setdefault(sample.name, {})[tuple(sorted(sample.labels.items()))] = sample.value
    return out


def test_metrics_report_tokens_missions_review_and_connectors(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, tmp_path: Any
) -> None:
    app = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "artifacts")
    seed_packs(app)
    sign_in_as(app, oidc, "bob")
    created = app.post(
        "/api/v1/missions",
        json={"playbook": "summarize-file", "inputs": {"file": "a.md"}, "start": False, "data_class": "C1"},
        headers=csrf(app),
    )
    mission_id = created.json()["id"]
    state = app.app.state.z  # type: ignore[attr-defined]

    async def spend() -> None:
        async with state.sessions() as db, db.begin():
            mission = await db.get(Mission, mission_id)
            assert mission is not None
            await usage.record(db, mission, "summarizer", "z-fast", 1200, 300, 0, datetime.now(UTC))
            await usage.record(db, mission, "summarizer", "z-fast", 800, 200, 0, datetime.now(UTC))

    app.portal.call(spend)  # type: ignore[union-attr]
    metrics = scrape(app)
    tokens = metrics["z_tokens_total"]
    team = "Cyber Ops"  # bob's team in the fixtures, reported by name
    assert tokens[(("agent", "summarizer"), ("direction", "in"), ("model", "z-fast"), ("team", team))] == 2000
    assert tokens[(("agent", "summarizer"), ("direction", "out"), ("model", "z-fast"), ("team", team))] == 500
    assert metrics["z_missions_active"][(("status", "draft"),)] >= 1
    assert metrics["z_review_pending"][(("kind", "action"),)] == 0
    assert set(metrics["z_connector_up"].values()) <= {0.0, 1.0} and metrics["z_connector_up"]
    assert metrics["z_notifications_pending"][()] >= 0
    assert "process_cpu_seconds_total" in metrics or "python_info" in metrics  # the process metrics stay
    assert all("mission" not in dict(labels) for labels in tokens)  # no high-cardinality labels
