"""ci/scripts/changelog.py: grouping of conventional commits and the range from the last tag."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from changelog import Commit, main, render, section_of


@pytest.mark.parametrize(
    ("subject", "body", "expected"),
    [
        ("feat(server): notifications", "", ("feat", "**server**: notifications")),
        ("fix: a bug", "", ("fix", "a bug")),
        ("feat(api)!: drop v0", "", ("breaking", "**api**: drop v0")),
        ("refactor: x", "BREAKING CHANGE: y", ("breaking", "x")),
        ("docs: ADR 0023", "", ("maintenance", "docs: ADR 0023")),
        ("Initial commit", "", ("maintenance", "Initial commit")),
    ],
)
def test_sections(subject: str, body: str, expected: tuple[str, str]) -> None:
    assert section_of(Commit("0" * 40, subject, body)) == expected


def test_render_orders_sections() -> None:
    text = render(
        [Commit("a" * 40, "docs: d", ""), Commit("b" * 40, "fix(ui): f", ""), Commit("c" * 40, "feat: n", "")],
        "v1.1.0",
        "2026-10-29",
    )
    assert text.index("### Features") < text.index("### Fixes") < text.index("### Maintenance")
    assert "- **ui**: f (bbbbbbbb)" in text and text.startswith("## v1.1.0 — 2026-10-29")


def test_range_starts_after_the_last_release_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def g(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@example.test")
    g("config", "user.name", "t")
    for i, subject in enumerate(["feat: one", "fix: two", "feat: three"]):
        (tmp_path / f"f{i}").write_text(str(i))
        g("add", ".")
        g("commit", "-q", "-m", subject)
        if i == 1:
            g("tag", "v1.0.0")
    monkeypatch.chdir(tmp_path)
    assert main(["--version", "v1.1.0"]) == 0
    out = capsys.readouterr().out
    assert "three" in out and "two" not in out and "one" not in out
