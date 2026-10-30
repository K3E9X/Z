"""The supply-chain guard scripts fail when they should."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "ci/scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_expired_and_incomplete_exceptions_fail(tmp_path: Path) -> None:
    check = _load("check_exceptions").check
    path = tmp_path / "exceptions.yaml"
    path.write_text(
        "exceptions:\n"
        "  - {id: CVE-1, scope: image:z-runner, reason: r, owner: '@a', expires: 2026-01-01}\n"
        "  - {id: CVE-2, scope: image:z-runner, reason: r, owner: '@a', expires: 2027-01-01}\n"
        "  - {id: CVE-3, scope: image:z-runner}\n"
    )
    problems = check(path, today=date(2026, 6, 1))
    assert len(problems) == 2 and "CVE-1" in problems[0] and "missing" in problems[1]


def test_repository_has_no_blocked_versions() -> None:
    assert _load("check_blocked_versions").check() == []


def test_blocked_litellm_image_is_detected(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = _load("check_blocked_versions")
    fake_root = tmp_path
    (fake_root / "security").mkdir()
    (fake_root / "security/blocked-versions.yaml").write_text((ROOT / "security/blocked-versions.yaml").read_text())
    (fake_root / "uv.lock").write_text('version = 1\n[[package]]\nname = "litellm"\nversion = "1.82.7"\n')
    (fake_root / "deploy").mkdir()
    (fake_root / "deploy/compose.yml").write_text("services:\n  litellm:\n    image: ghcr.io/berriai/litellm:v1.82.8\n")
    monkeypatch.setattr(module, "ROOT", fake_root)
    problems = module.check()
    assert any("uv.lock: litellm 1.82.7" in p for p in problems)
    assert any("litellm:1.82.8" in p for p in problems)
