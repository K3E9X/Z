"""Dependency findings block on severity (osv) or available fixes (pip-audit), unless excepted."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
import vuln_gate


@pytest.fixture
def exceptions(tmp_path: Path) -> Path:
    path = tmp_path / "exceptions.yaml"
    soon = date.today() + timedelta(days=30)
    path.write_text(
        "exceptions:\n"
        f"  - {{id: CVE-2026-9, scope: deps:pypi, reason: r, owner: '@a', expires: {soon}}}\n"
        f"  - {{id: GHSA-npm-1, scope: deps:npm, reason: r, owner: '@a', expires: {soon}}}\n"
    )
    return path


def osv_report() -> dict[str, object]:
    def pkg(eco: str, name: str, groups: list[dict[str, object]]) -> dict[str, object]:
        return {"package": {"ecosystem": eco, "name": name, "version": "1.0"}, "groups": groups}

    return {
        "results": [
            {
                "source": {"path": "/repo/uv.lock", "type": "lockfile"},
                "packages": [
                    pkg("PyPI", "high", [{"ids": ["GHSA-a"], "aliases": ["CVE-1"], "max_severity": "8.1"}]),
                    pkg("PyPI", "low", [{"ids": ["GHSA-b"], "max_severity": "3.1"}]),
                    pkg("PyPI", "unscored", [{"ids": ["PYSEC-c"], "max_severity": ""}]),
                    pkg("PyPI", "excepted", [{"ids": ["GHSA-d"], "aliases": ["CVE-2026-9"], "max_severity": "9.8"}]),
                ],
            },
            {
                "source": {"path": "/repo/ui/pnpm-lock.yaml", "type": "lockfile"},
                "packages": [
                    pkg("npm", "a", [{"ids": ["GHSA-npm-1"], "max_severity": "9.0"}]),
                    pkg("npm", "b", [{"ids": ["CVE-2026-9"], "max_severity": "9.0"}]),  # pypi exception only
                ],
            },
        ]
    }


def test_osv_gate(exceptions: Path) -> None:
    failures, warnings = vuln_gate.osv(osv_report(), exceptions)
    assert failures == [
        "uv.lock: high 1.0: GHSA-a (score 8.1)",
        "uv.lock: unscored 1.0: PYSEC-c (no score (treated as high))",
        "pnpm-lock.yaml: b 1.0: CVE-2026-9 (score 9.0)",
    ]
    assert len(warnings) == 3 and sum("accepted exception" in w for w in warnings) == 2


def test_pip_audit_gate(exceptions: Path) -> None:
    report = {
        "dependencies": [
            {"name": "a", "version": "1", "vulns": [{"id": "PYSEC-1", "fix_versions": ["1.1"], "aliases": []}]},
            {"name": "b", "version": "1", "vulns": [{"id": "PYSEC-2", "fix_versions": [], "aliases": []}]},
            {
                "name": "c",
                "version": "1",
                "vulns": [{"id": "PYSEC-3", "fix_versions": ["2"], "aliases": ["CVE-2026-9"]}],
            },
            {"name": "d", "version": "1", "vulns": []},
        ]
    }
    failures, warnings = vuln_gate.pip_audit(report, exceptions)
    assert failures == ["a 1: PYSEC-1 (fixed in 1.1)"]
    assert warnings == ["b 1: PYSEC-2 (no fix yet)", "c 1: PYSEC-3: accepted exception"]


def test_cli(tmp_path: Path, exceptions: Path) -> None:
    report = tmp_path / "osv.json"
    report.write_text(json.dumps({"results": []}))
    assert vuln_gate.main(["osv", str(report), "--exceptions", str(exceptions)]) == 0
    report.write_text(json.dumps(osv_report()))
    assert vuln_gate.main(["osv", str(report), "--exceptions", str(exceptions)]) == 1
    report.write_text("not json")
    assert vuln_gate.main(["pip-audit", str(report), "--exceptions", str(exceptions)]) == 1
    bad = tmp_path / "bad.yaml"
    bad.write_text("exceptions: [1]\n")
    report.write_text(json.dumps({"dependencies": []}))
    assert vuln_gate.main(["pip-audit", str(report), "--exceptions", str(bad)]) == 1
