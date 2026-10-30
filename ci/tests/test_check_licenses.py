"""ci/scripts/check_licenses.py: SPDX-like expressions, metadata discovery, policy overrides."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from check_licenses import Package, check, load_policy, main, node_packages, python_packages, verdict

POLICY = """
allow: [MIT, Apache-2.0, BSD-3-Clause, LGPL-3.0-only]
deny: ["^A?GPL-", "^SSPL"]
packages:
  - { ecosystem: pypi, name: odd_pkg, license: MIT, reason: "checked by hand" }
ignore:
  - { ecosystem: pypi, name: z-server }
"""


@pytest.fixture
def policy(tmp_path: Path):  # type: ignore[no-untyped-def]
    path = tmp_path / "licenses.yaml"
    path.write_text(POLICY, encoding="utf-8")
    return load_policy(path)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("MIT", "allowed"),
        ("Apache 2.0", "allowed"),
        ("Apache License, Version 2.0", "allowed"),
        ("Apache-2.0", "allowed"),
        ("MIT OR GPL-3.0-only", "allowed"),
        ("GPL-3.0-only", "denied"),
        ("AGPL-3.0-or-later", "denied"),
        ("MIT AND GPL-2.0-only", "denied"),
        ("MIT AND Apache-2.0", "allowed"),
        ("(MIT OR Unknown-1.0) AND BSD-3-Clause", "allowed"),
        ("Unknown-1.0 OR SSPL-1.0", "denied"),
        ("LicenseRef-Proprietary", "unknown"),
        ("Apache-2.0 WITH LLVM-exception", "allowed"),
        ("MIT/Apache-2.0", "allowed"),
    ],
)
def test_expressions(policy, expression: str, expected: str) -> None:  # type: ignore[no-untyped-def]
    assert verdict(expression, policy) == expected


def test_overrides_ignores_and_missing_metadata(policy) -> None:  # type: ignore[no-untyped-def]
    problems = check(
        [
            Package("pypi", "Odd.Pkg", "1", None),  # reviewed, PEP 503 name
            Package("pypi", "z_server", "0.1", None),  # our own
            Package("pypi", "mystery", "2", None),
            Package("npm", "left-pad", "1", "WTFPL"),
        ],
        policy,
    )
    assert problems == [
        "pypi mystery 2: no license metadata (review it in the policy)",
        "npm left-pad 1: 'WTFPL' is unknown",
    ]


def test_discovery_reads_installed_metadata(tmp_path: Path) -> None:
    site = tmp_path / "site"
    for name, headers in {
        "expr-1.0": "Name: expr\nVersion: 1.0\nLicense-Expression: MIT\n",
        "classic-2.0": "Name: classic\nVersion: 2.0\nClassifier: License :: OSI Approved :: BSD License\n",
        "short-3.0": "Name: short\nVersion: 3.0\nLicense: Apache 2.0\n",
    }.items():
        (site / f"{name}.dist-info").mkdir(parents=True)
        (site / f"{name}.dist-info" / "METADATA").write_text(f"Metadata-Version: 2.4\n{headers}\n", encoding="utf-8")
    found = {p.name: p.license for p in python_packages(site)}
    assert found == {"expr": "MIT", "classic": "BSD-3-Clause", "short": "Apache 2.0"}

    nm = tmp_path / "node_modules"
    for pkg, data in {
        "a@1.0.0/node_modules/a": {"name": "a", "version": "1.0.0", "license": "MIT"},
        "@s+b@2.0.0/node_modules/@s/b": {"name": "@s/b", "version": "2.0.0", "license": {"type": "ISC"}},
        "c@3.0.0/node_modules/c": {"name": "c", "version": "3.0.0", "licenses": [{"type": "MIT"}, {"type": "GPL-2.0"}]},
    }.items():
        (nm / ".pnpm" / pkg).mkdir(parents=True)
        (nm / ".pnpm" / pkg / "package.json").write_text(json.dumps(data), encoding="utf-8")
    assert {p.name: p.license for p in node_packages(nm)} == {"a": "MIT", "@s/b": "ISC", "c": "MIT OR GPL-2.0"}


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy = tmp_path / "licenses.yaml"
    policy.write_text(POLICY, encoding="utf-8")
    site = tmp_path / "site"
    (site / "bad-1.dist-info").mkdir(parents=True)
    (site / "bad-1.dist-info" / "METADATA").write_text("Name: bad\nVersion: 1\nLicense-Expression: GPL-3.0-only\n")
    assert main(["--python", str(site), "--policy", str(policy)]) == 1
    assert "pypi bad 1: 'GPL-3.0-only' is denied" in capsys.readouterr().out
    assert main(["--python", str(tmp_path / "empty"), "--policy", str(policy)]) == 2
