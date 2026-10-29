"""Pack seed, agents (create, rename, duplicate, versions), skills (versions, rollback), playbooks, exposure review."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import REPO, csrf, seed_packs, sign_in_as, use_jar


@pytest.fixture
def seeded(client: TestClient) -> TestClient:
    seed_packs(client)
    return client


@pytest.fixture
def admin(seeded: TestClient, oidc: FakeGitLabOIDC) -> TestClient:
    sign_in_as(seeded, oidc, "alice")
    return seeded


def test_seed_loads_every_pack_and_is_idempotent(client: TestClient) -> None:
    first = seed_packs(client)
    assert any(c.startswith("agent vuln-analyst@") for c in first.created)
    assert any(c.startswith("connector logos") for c in first.created)
    second = seed_packs(client)
    assert second.created == [] and second.updated == []


def test_seed_refuses_packs_with_lint_errors(client: TestClient, tmp_path: Path) -> None:
    from z_server.catalog.seed import SeedError

    packs = tmp_path / "packs"
    shutil.copytree(REPO / "packs", packs)
    path = packs / "vuln/playbooks/weekly-vuln-triage.yaml"
    data = yaml.safe_load(path.read_text())
    data["team"] = ["vuln-analyst"]
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(SeedError):
        seed_packs(client, packs)


def test_pack_update_moves_untouched_items_and_keeps_local_edits(admin: TestClient, tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    shutil.copytree(REPO / "packs", packs)
    skill = packs / "demo/skills/summarize/SKILL.md"
    skill.write_text(
        skill.read_text()
        .replace("version: 1.0.0", "version: 1.1.0")
        .replace("exactly 3 bullet points", "exactly 3 short bullet points")
    )
    playbook = packs / "demo/playbooks/summarize-file.yaml"
    playbook.write_text(playbook.read_text().replace("version: 1.0.0", "version: 1.1.0"))
    # a local edit of the agent: it must survive the pack update
    admin.patch("/api/v1/agents/summarizer", json={"display_name": "Sumo", "handle": "sumo"}, headers=csrf(admin))
    report = seed_packs(admin, packs)
    assert "skill summarize@1.1.0" in report.updated and "playbook summarize-file@1.1.0" in report.updated
    assert any(k.startswith("agent summarizer") for k in report.kept)
    assert admin.get("/api/v1/agents/sumo").json()["display_name"] == "Sumo"
    versions = [v["version"] for v in admin.get("/api/v1/skills/summarize/versions").json()]
    assert versions == ["1.1.0", "1.0.0"]


def test_agents_list_get_by_handle(admin: TestClient) -> None:
    agents = admin.get("/api/v1/agents", params={"limit": 200}).json()["items"]
    assert {a["name"] for a in agents} >= {"vuln-analyst", "orchestrator", "summarizer", "dev-engineer"}
    vega = admin.get("/api/v1/agents/@vuln-analyst").json()
    assert vega["model"] == "z-fast" and vega["status"] == "available" and vega["pack"] == "vuln"
    assert admin.get("/api/v1/agents/nobody").status_code == 404


def test_rename_keeps_name_and_version_and_is_reachable_by_new_handle(admin: TestClient) -> None:
    renamed = admin.patch(
        "/api/v1/agents/dev-engineer",
        json={"display_name": "Atlas", "handle": "atlas", "avatar": {"initials": "AT", "color": "#6AA6FF"}},
        headers=csrf(admin),
    )
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["name"] == "dev-engineer" and body["handle"] == "atlas" and body["version"] == "1.0.0"
    assert admin.get("/api/v1/agents/@atlas").json()["display_name"] == "Atlas"
    assert len(admin.get("/api/v1/agents/atlas/versions").json()) == 1  # rename does not create a version
    assert admin.patch("/api/v1/agents/atlas", json={"name": "other"}, headers=csrf(admin)).status_code == 422
    events = admin.get("/api/v1/audit", params={"action": "agent.renamed"}).json()["items"]
    assert events[0]["before"]["handle"] == "dev-engineer" and events[0]["after"]["handle"] == "atlas"


def test_profile_change_creates_a_version(admin: TestClient) -> None:
    seeded = admin.get("/api/v1/agents/vuln-analyst").json()["version"]  # the pack's version
    major, minor, _ = (int(x) for x in seeded.split("."))
    bumped = f"{major}.{minor + 1}.0"
    changed = admin.patch(
        "/api/v1/agents/vuln-analyst", json={"model": "z-heavy", "change_note": "more reasoning"}, headers=csrf(admin)
    ).json()
    assert changed["version"] == bumped and changed["model"] == "z-heavy"
    versions = admin.get("/api/v1/agents/vuln-analyst/versions").json()
    assert [v["version"] for v in versions] == [bumped, seeded] and versions[0]["change_note"] == "more reasoning"


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"tools": ["read", "gateway:jira.issues.create"]}, "writes are proposed"),
        ({"skills": ["no-such-skill"]}, "does not exist"),
        ({"model": "ext-gpt"}, "not a configured model alias"),
        ({"handle": "orchestrator"}, "already used"),
        ({"tools": ["gateway:nowhere.things.*"]}, "unknown connector"),
    ],
)
def test_agent_validation(admin: TestClient, patch: dict[str, object], message: str) -> None:
    resp = admin.patch("/api/v1/agents/ticket-manager", json=patch, headers=csrf(admin))
    assert resp.status_code == 422 and message in resp.text


def test_create_from_template_and_duplicate(admin: TestClient) -> None:
    vega = admin.post(
        "/api/v1/agents",
        json={"template": "vuln-analyst", "display_name": "Vega", "handle": "vega"},
        headers=csrf(admin),
    )
    assert vega.status_code == 201, vega.text
    assert (
        vega.json()["template"] == "vuln-analyst"
        and vega.json()["name"] == "vega"
        and vega.json()["version"] == "1.0.0"
    )
    copy = admin.post(
        "/api/v1/agents/vega/duplicate", json={"display_name": "Vega 2", "handle": "vega"}, headers=csrf(admin)
    )
    assert copy.status_code == 422  # handle taken
    copy = admin.post(
        "/api/v1/agents/vega/duplicate", json={"display_name": "Vega 2", "handle": "vega2"}, headers=csrf(admin)
    )
    assert copy.status_code == 201 and copy.json()["template"] == "vega"


def test_create_full_profile(admin: TestClient) -> None:
    profile = yaml.safe_load((REPO / "packs/demo/agents/summarizer.yaml").read_text())
    profile.update(name="notes", display_name="Notes", handle="notes")
    assert admin.post("/api/v1/agents", json=profile, headers=csrf(admin)).status_code == 201
    profile["unknown_key"] = 1
    profile.update(name="notes2", handle="notes2")
    assert admin.post("/api/v1/agents", json=profile, headers=csrf(admin)).status_code == 422


def test_builder_can_edit_but_operator_cannot(seeded: TestClient, oidc: FakeGitLabOIDC) -> None:
    builder = sign_in_as(seeded, oidc, "erin")
    operator = sign_in_as(seeded, oidc, "bob")
    use_jar(seeded, operator)
    assert (
        seeded.patch(
            "/api/v1/agents/summarizer", json={"display_name": "X", "handle": "x"}, headers=csrf(seeded)
        ).status_code
        == 403
    )
    use_jar(seeded, builder)
    assert (
        seeded.patch(
            "/api/v1/agents/summarizer", json={"display_name": "X", "handle": "x"}, headers=csrf(seeded)
        ).status_code
        == 200
    )


def test_skills(admin: TestClient) -> None:
    skills = admin.get("/api/v1/skills", params={"limit": 200}).json()["items"]
    triage = next(s for s in skills if s["name"] == "triage-finding")
    assert triage["origin"] == "pack" and triage["agents"] == ["vuln-analyst"] and "content" not in triage
    detail = admin.get("/api/v1/skills/triage-finding").json()
    assert detail["content"].startswith("---") and "triage-result.json" in detail["content"]
    by_agent = admin.get("/api/v1/skills", params={"agent": "vuln-analyst"}).json()["items"]
    assert {s["name"] for s in by_agent} == {"triage-finding", "environment-context", "contextual-cvss"}


def test_skill_rollback(admin: TestClient, tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    shutil.copytree(REPO / "packs", packs)
    skill = packs / "demo/skills/summarize/SKILL.md"
    skill.write_text(skill.read_text().replace("version: 1.0.0", "version: 1.1.0") + "\n6. Extra rule.\n")
    seed_packs(admin, packs)
    assert admin.get("/api/v1/skills/summarize").json()["version"] == "1.1.0"
    back = admin.post(
        "/api/v1/skills/summarize/rollback", json={"to_version": "1.0.0", "reason": "regression"}, headers=csrf(admin)
    )
    assert back.status_code == 200 and back.json()["version"] == "1.0.0" and "Extra rule" not in back.json()["content"]
    assert back.json()["last_change"]["kind"] == "rolled_back"
    assert (
        admin.post("/api/v1/skills/summarize/rollback", json={"to_version": "1.0.0"}, headers=csrf(admin)).status_code
        == 409
    )
    assert (
        admin.post("/api/v1/skills/summarize/rollback", json={"to_version": "9.9.9"}, headers=csrf(admin)).status_code
        == 404
    )


def test_playbooks(admin: TestClient) -> None:
    triage = admin.get("/api/v1/playbooks/weekly-vuln-triage").json()
    assert triage["version"] == "1.6.0" and triage["usage_count"] == 0 and triage["pack"] == "vuln"
    assert {
        p["name"] for p in admin.get("/api/v1/playbooks", params={"domain": "code", "limit": 200}).json()["items"]
    } == {"bulk-dependency-upgrade", "mr-security-review"}
    manifest = {
        k: v
        for k, v in triage.items()
        if k not in {"id", "pack", "owner_team", "usage_count", "last_run_at", "created_at", "updated_at"}
    }
    manifest["description"] = "Triage with a new description"
    patched = admin.patch("/api/v1/playbooks/weekly-vuln-triage", json=manifest, headers=csrf(admin)).json()
    assert patched["version"] == "1.7.0" and patched["description"] == "Triage with a new description"
    broken = {**manifest, "team": ["vuln-analyst"]}
    assert admin.patch("/api/v1/playbooks/weekly-vuln-triage", json=broken, headers=csrf(admin)).status_code == 422


def test_create_playbook_with_pack_qualified_schema(admin: TestClient) -> None:
    body = {
        "name": "quick-summary",
        "version": "1.0.0",
        "description": "Summarize a file",
        "team": ["summarizer"],
        "steps": [
            {
                "id": "s",
                "agent": "summarizer",
                "prompt": "Summarize {{ inputs.file }}",
                "output": {"schema": "demo/schemas/summary.json"},
            }
        ],
        "autonomy": "read_only",
    }
    assert admin.post("/api/v1/playbooks", json=body, headers=csrf(admin)).status_code == 201
    assert admin.post("/api/v1/playbooks", json=body, headers=csrf(admin)).status_code == 409
    bad = {
        **body,
        "name": "bad-summary",
        "steps": [{**body["steps"][0], "output": {"schema": "../../etc/passwd.json"}}],
    }
    assert admin.post("/api/v1/playbooks", json=bad, headers=csrf(admin)).status_code == 422


def test_connectors_start_pending_review_with_nothing_exposed(admin: TestClient) -> None:
    connectors = {c["name"]: c for c in admin.get("/api/v1/connectors").json()}
    assert set(connectors) >= {"gitlab", "jira", "logos", "defectdojo", "cmdb", "confluence", "web", "m365"}
    assert all(c["review_status"] == "pending_review" and c["operations_exposed"] == 0 for c in connectors.values())
    assert "@vuln-analyst" not in connectors["cmdb"]["used_by"] and "vuln-analyst" in connectors["cmdb"]["used_by"]
    ops = {o["id"]: o for o in admin.get("/api/v1/connectors/logos/operations").json()}
    assert (
        ops["logos.accounts.disable"]["class"] == "destructive"
        and ops["logos.accounts.disable"]["proposed_exposed"] is True
    )
    assert ops["logos.accounts.delete"]["proposed_exposed"] is False


def test_exposure_review_and_class_floor(admin: TestClient) -> None:
    ops = admin.get("/api/v1/connectors/logos/operations").json()
    decisions = [{"id": o["id"], "exposed": o["proposed_exposed"]} for o in ops]
    approved = admin.post(
        "/api/v1/connectors/logos/approve-exposure", json={"operations": decisions}, headers=csrf(admin)
    ).json()
    assert approved["review_status"] == "approved" and approved["operations_exposed"] == 3
    lower = admin.patch("/api/v1/operations/logos.accounts.disable", json={"class": "write"}, headers=csrf(admin))
    assert lower.status_code == 422
    raised = admin.patch("/api/v1/operations/logos.assets.get", json={"class": "write"}, headers=csrf(admin))
    assert raised.status_code == 200 and raised.json()["class"] == "write"
    hidden = admin.patch("/api/v1/operations/logos.accounts.list", json={"exposed": False}, headers=csrf(admin))
    assert hidden.json()["exposed"] is False and hidden.json()["reviewed_by"] == "alice"
    unknown = admin.post(
        "/api/v1/connectors/logos/approve-exposure",
        json={"operations": [{"id": "logos.nope.get", "exposed": True}]},
        headers=csrf(admin),
    )
    assert unknown.status_code == 422


def test_only_admins_review_exposure(seeded: TestClient, oidc: FakeGitLabOIDC) -> None:
    sign_in_as(seeded, oidc, "erin")
    resp = seeded.post("/api/v1/connectors/logos/approve-exposure", json={"operations": []}, headers=csrf(seeded))
    assert resp.status_code == 403
