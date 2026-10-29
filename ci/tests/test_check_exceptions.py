"""Security exceptions: malformed, unknown, expired and over-long entries fail; active ids reach scanners."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import check_exceptions as ce
import pytest

TODAY = date(2026, 6, 1)


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "exceptions.yaml"
    path.write_text(body)
    return path


def test_repository_file_is_valid() -> None:
    assert ce.check(Path(__file__).resolve().parents[2] / "security/exceptions.yaml") == []


def test_empty_and_null_lists_are_fine(tmp_path: Path) -> None:
    assert ce.check(write(tmp_path, "exceptions: []\n"), TODAY) == []
    assert ce.check(write(tmp_path, "exceptions:\n"), TODAY) == []
    assert ce.check(write(tmp_path, "# nothing\n"), TODAY) == []


def test_every_problem_is_reported(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "exceptions:\n"
        "  - {id: CVE-1, scope: image:z-runner, reason: r, owner: '@a', expires: 2026-01-01}\n"  # expired
        "  - {id: CVE-2, scope: image:z-runner, reason: r, owner: '@a', expires: 2027-01-01}\n"  # fine
        "  - {id: CVE-3, scope: image:z-runner}\n"  # incomplete
        "  - {id: CVE-4, scope: everything, reason: r, owner: '@a', expires: 2026-07-01}\n"  # unknown scope
        "  - {id: CVE-5, scope: '*', reason: r, owner: '@a', expires: 2029-01-01}\n"  # too far ahead
        "  - {id: CVE-6, scope: iac, reason: r, owner: '@a', expires: 2026-13-01}\n"  # not a date
        "  - {id: CVE-2, scope: image:z-runner, reason: r, owner: '@a', expires: 2026-08-01}\n"  # duplicate
        "  - {id: CVE-7, scope: sast, reason: r, owner: '@a', expires: 2026-08-01, expire: x}\n"  # typo key
        "  - {id: 'bad id', scope: sast, reason: r, owner: '@a', expires: 2026-08-01}\n",
    )
    problems = ce.check(path, TODAY)
    assert len(problems) == 8, problems
    joined = "\n".join(problems)
    for needle in ("CVE-1 (image:z-runner): expired", "#2: missing", "CVE-4 (everything): unknown scope"):
        assert needle in joined
    for needle in (
        "CVE-5 (*): expires 2029-01-01",
        "CVE-6 (iac): expires must be",
        "CVE-2 (image:z-runner): duplicate",
    ):
        assert needle in joined
    assert "#7: unknown keys ['expire']" in joined and "bad id (sast): invalid id" in joined


@pytest.mark.parametrize("body", ["- a\n", "exceptions: {a: 1}\n", "other: []\n", "exceptions: [1]\n", "a: &x 1\n"])
def test_malformed_files_fail(tmp_path: Path, body: str) -> None:
    assert ce.check(write(tmp_path, body), TODAY)


def test_missing_file_fails(tmp_path: Path) -> None:
    assert ce.check(tmp_path / "nope.yaml", TODAY)


def test_active_ids_by_scope(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "exceptions:\n"
        "  - {id: CVE-1, scope: 'image:*', reason: r, owner: '@a', expires: 2026-07-01}\n"
        "  - {id: CVE-2, scope: image:z-ui, reason: r, owner: '@a', expires: 2026-07-01}\n"
        "  - {id: CVE-3, scope: deps:pypi, reason: r, owner: '@a', expires: 2026-07-01}\n"
        "  - {id: CVE-4, scope: '*', reason: r, owner: '@a', expires: 2026-07-01}\n"
        "  - {id: CVE-5, scope: image:z-ui, reason: r, owner: '@a', expires: 2026-05-01}\n",  # expired
    )
    ids = lambda scope: [e["id"] for e in ce.active(path, scope, TODAY)]  # noqa: E731
    assert ids("image:z-ui") == ["CVE-1", "CVE-2", "CVE-4"]
    assert ids("image:z-server") == ["CVE-1", "CVE-4"]
    assert ids("deps:pypi") == ["CVE-3", "CVE-4"]
    assert ids("deps:npm") == ["CVE-4"]


def test_osv_toml_is_escaped() -> None:
    toml = ce.osv_toml(
        [{"id": "GHSA-1", "scope": "deps:pypi", "reason": 'say "no"', "owner": "@a", "expires": "2026-07-01"}]
    )
    assert (
        toml
        == '[[IgnoredVulns]]\nid = "GHSA-1"\nignoreUntil = 2026-07-01T23:59:59Z\nreason = "say \\"no\\" (owner @a)"\n'
    )


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = write(
        tmp_path, "exceptions:\n  - {id: CVE-1, scope: deps:npm, reason: r, owner: '@a', expires: 2099-01-01}\n"
    )
    assert ce.main([str(good), "--today", "2098-06-01"]) == 0
    assert ce.main([str(good)]) == 1  # 2099 is more than a year ahead today
    capsys.readouterr()
    empty = write(tmp_path, "exceptions: []\n")
    assert ce.main(["ids", "deps:npm", str(empty)]) == 0
    assert capsys.readouterr().out == ""
    bad = tmp_path / "bad.yaml"
    bad.write_text("exceptions: [1]\n")
    assert ce.main(["ids", "deps:npm", str(bad)]) == 1
