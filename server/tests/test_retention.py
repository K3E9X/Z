"""Retention sweep (docs/07 §6): ended missions lose old events and artifacts; running ones keep everything."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from z_server import events
from z_server.db.models import Artifact, AuditEvent, Event, Mission
from z_server.ids import new_id
from z_server.missions.service import utcnow
from z_server.workers import retention

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.server import csrf, seed_packs, sign_in_as


def test_sweep_removes_only_what_retention_allows(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC, tmp_path: Any
) -> None:
    app = make_client(llm_keys=RecordingKeyAdmin(), artifacts_dir=tmp_path / "a")
    seed_packs(app)
    sign_in_as(app, oidc, "bob")
    plan = {"steps": [{"id": "s", "agent": "summarizer", "prompt": "x"}]}
    ids = []
    for title in ("ended", "running"):
        resp = app.post(
            "/api/v1/missions", json={"title": title, "prompt": "x", "plan": plan, "start": False}, headers=csrf(app)
        )
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id"])
    ended, running = ids
    state = app.app.state.z  # type: ignore[attr-defined]
    now = utcnow()
    files: dict[str, Path] = {}

    async def setup() -> None:
        async with state.sessions() as db, db.begin():
            await db.execute(
                update(Mission)
                .where(Mission.id == ended)
                .values(status="succeeded", ended_at=now - timedelta(days=200))
            )
            for mission_id in ids:
                await events.append(db, mission_id, "thought", {"text": "old"})
                for name, data_class, age in (("c3", "C3", 8), ("c2", "C2", 8), ("old", "C2", 100)):
                    path = tmp_path / f"{mission_id}-{name}"
                    path.write_bytes(b"x")
                    aid = new_id("art")
                    files[f"{mission_id}:{name}"] = path
                    db.add(
                        Artifact(
                            id=aid,
                            mission_id=mission_id,
                            kind="file",
                            name=name,
                            content_type="text/plain",
                            size=1,
                            sha256="0" * 64,
                            data_class=data_class,
                            storage_path=str(path),
                            created_at=now - timedelta(days=age),
                        )
                    )

    app.portal.call(setup)  # type: ignore[union-attr]

    async def audit_count() -> int:
        async with state.sessions() as db:
            return int((await db.execute(select(func.count()).select_from(AuditEvent))).scalar_one())

    audited = app.portal.call(audit_count)  # type: ignore[union-attr]
    swept = app.portal.call(retention.sweep, state)  # type: ignore[union-attr]
    assert swept["artifacts"] == 2 and swept["events"] > 0, swept

    async def left() -> tuple[dict[str, int], set[str]]:
        async with state.sessions() as db:
            counts = {
                mid: int((await db.execute(select(func.count()).where(Event.mission_id == mid))).scalar_one())
                for mid in ids
            }
            names = {f"{a.mission_id}:{a.name}" for a in (await db.execute(select(Artifact))).scalars()}
            return counts, names

    counts, names = app.portal.call(left)  # type: ignore[union-attr]
    assert counts[ended] == 0 and counts[running] > 0
    assert names == {f"{ended}:c2", f"{running}:c3", f"{running}:c2", f"{running}:old"}
    assert not files[f"{ended}:c3"].exists() and not files[f"{ended}:old"].exists() and files[f"{ended}:c2"].exists()
    assert app.portal.call(audit_count) == audited  # type: ignore[union-attr]  # the audit log is never swept
