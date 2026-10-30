"""GET /skills/{id}/versions/{version} (getSkillVersion): one version of a skill with its SKILL.md."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import REPO, seed_packs, sign_in_as


def test_each_version_keeps_its_own_content(client: TestClient, oidc: FakeGitLabOIDC, tmp_path: Path) -> None:
    seed_packs(client)
    packs = tmp_path / "packs"
    shutil.copytree(REPO / "packs", packs)
    skill = packs / "demo/skills/summarize/SKILL.md"
    skill.write_text(skill.read_text().replace("version: 1.0.0", "version: 1.1.0") + "\n6. Extra rule.\n")
    seed_packs(client, packs)
    sign_in_as(client, oidc, "victor")  # a viewer: skills:read is enough

    old = client.get("/api/v1/skills/summarize/versions/1.0.0")
    assert old.status_code == 200, old.text
    new = client.get("/api/v1/skills/summarize/versions/1.1.0").json()
    first = old.json()
    assert first["version"] == "1.0.0" and first["origin"] == "pack" and "Extra rule" not in first["content"]
    assert new["version"] == "1.1.0" and new["content"].rstrip().endswith("6. Extra rule.")
    assert new["checksum"] != first["checksum"]
    listed = {v["version"]: v for v in client.get("/api/v1/skills/summarize/versions").json()}
    assert {k: v for k, v in new.items() if k != "content"} == listed["1.1.0"]
    current = client.get("/api/v1/skills/summarize").json()
    assert current["content"] == new["content"] and current["checksum"] == new["checksum"]
    by_id = client.get(f"/api/v1/skills/{current['id']}/versions/1.0.0")
    assert by_id.status_code == 200 and by_id.json()["content"] == first["content"]


def test_unknown_version_or_skill_is_a_404_problem(client: TestClient, oidc: FakeGitLabOIDC) -> None:
    seed_packs(client)
    unauthenticated = client.get("/api/v1/skills/summarize/versions/1.0.0")
    assert unauthenticated.status_code == 401
    sign_in_as(client, oidc, "victor")
    missing = client.get("/api/v1/skills/summarize/versions/9.9.9")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["type"] == "urn:z:problem:not-found" and "no version 9.9.9" in missing.json()["detail"]
    assert client.get("/api/v1/skills/no-such-skill/versions/1.0.0").status_code == 404
