"""Blocked versions are found in every place the repository pins software."""

from __future__ import annotations

import shutil
from pathlib import Path

import check_blocked as cb
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "security").mkdir()
    shutil.copy(ROOT / "security/blocked-versions.yaml", tmp_path / "security/blocked-versions.yaml")
    return tmp_path


def put(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_repository_has_no_blocked_versions() -> None:
    assert cb.check(ROOT) == []


def test_clean_fake_repo(repo: Path) -> None:
    put(repo, "uv.lock", 'version = 1\n[[package]]\nname = "litellm"\nversion = "1.82.6"\n')
    assert cb.check(repo) == []


def test_python_locks(repo: Path) -> None:
    put(repo, "uv.lock", 'version = 1\n[[package]]\nname = "LiteLLM"\nversion = "1.82.7"\n')
    put(repo, "ci/scripts/requirements/x.txt", "telnyx==4.87.2 \\\n    --hash=sha256:00\nok==1.0\n")
    problems = cb.check(repo)
    assert any(p.startswith("uv.lock: LiteLLM 1.82.7 is blocked") for p in problems)
    assert any(p.startswith("ci/scripts/requirements/x.txt: telnyx 4.87.2") for p in problems)


def test_npm_lock(repo: Path) -> None:
    put(
        repo,
        "security/blocked-versions.yaml",
        "blocked:\n"
        '  - {ecosystem: npm, name: "@evil/pkg", versions: ["1.2.3"], reason: r}\n'
        '  - {ecosystem: npm, name: left-pad, versions: ["9.9.9"], reason: r}\n',
    )
    put(
        repo,
        "ui/pnpm-lock.yaml",
        "lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n      left-pad:\n        version: 9.9.9\n"
        "packages:\n  '@evil/pkg@1.2.3':\n    resolution: {integrity: sha512-x}\n  left-pad@1.0.0:\n    resolution: {}\n"
        "snapshots:\n  left-pad@9.9.9(react@19.0.0):\n    dependencies: {}\n",
    )
    problems = cb.check(repo)
    assert any("@evil/pkg 1.2.3" in p for p in problems)
    assert any("left-pad 9.9.9" in p for p in problems)
    assert not any("left-pad 1.0.0" in p for p in problems)


def test_scanner_lock(repo: Path) -> None:
    lock = repo / "ci/scripts/versions.lock"
    put(
        repo,
        "ci/scripts/versions.lock",
        "# tool version arch sha256 kind url\ntrivy 0.69.5 amd64 00 tgz:trivy https://x\n",
    )
    assert cb.check_lock(lock, repo) == ["versions.lock: trivy 0.69.5 is blocked (Compromised releases, March 2026)"]
    put(repo, "ci/scripts/versions.lock", "trivy v0.76.1 amd64 00 tgz:trivy https://x\n")
    assert cb.check_lock(lock, repo) == []
    assert cb.main(["--root", str(repo), "--lock", str(lock)]) == 0


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("deploy/compose/docker-compose.yml", "services:\n  l:\n    image: litellm/litellm:main-v1.82.7@sha256:00\n"),
        ("deploy/images/x/Dockerfile", "FROM ghcr.io/berriai/litellm:v1.82.8 AS base\n"),
        (
            "deploy/images/x/Dockerfile",
            "ARG LITELLM_IMAGE=docker.io/litellm/litellm:1.82.7-stable\nFROM ${LITELLM_IMAGE}\n",
        ),
        (".github/workflows/x.yml", "jobs:\n  a:\n    container:\n      image: aquasec/trivy:0.69.4\n"),
        (
            "deploy/helm/z/values.yaml",
            "litellm:\n  image:\n    repository: docker.io/litellm/litellm\n    tag: v1.82.8\n",
        ),
        (".gitlab-ci.yml", "scan:\n  image: 'registry.local:5000/aquasec/trivy:0.69.6'\n"),
    ],
)
def test_images(repo: Path, rel: str, text: str) -> None:
    put(repo, rel, text)
    problems = cb.check(repo)
    assert len(problems) == 1 and problems[0].startswith(rel), problems


def test_images_that_only_look_similar(repo: Path) -> None:
    put(
        repo,
        "deploy/a.yml",
        "image: notlitellm/litellm-ui:1.82.7\nimage: litellm/litellm:v1.105.0\nimage: x/trivy:0.69.40\n",
    )
    assert cb.check(repo) == []


@pytest.mark.parametrize(
    "body",
    [
        "blocked: {}\n",
        "blocked:\n  - {ecosystem: cargo, name: x, versions: ['1'], reason: r}\n",
        "blocked:\n  - {ecosystem: pypi, name: x, versions: [], reason: r}\n",
        "blocked:\n  - {ecosystem: pypi, name: x, versions: ['1']}\n",
    ],
)
def test_bad_rules_fail_closed(repo: Path, body: str) -> None:
    put(repo, "security/blocked-versions.yaml", body)
    assert cb.main(["--root", str(repo)]) == 1


def test_missing_rules_fail_closed(tmp_path: Path) -> None:
    assert cb.main(["--root", str(tmp_path)]) == 1
