"""Changelog from conventional commits (docs/13 §1 "release"). Stdlib only.

    changelog.py [--from REF] [--to REF] [--version NAME]     print a Markdown section for the range

`--from` defaults to the latest `vX.Y.Z` tag reachable from `--to` (default HEAD), or the first commit. Commits
are grouped by type (feat, fix, security-relevant `fix(security)`/`!` breaking changes first); merge commits and
the types docs/test/ci/build/chore/style/refactor go under "Maintenance" in one line each, so a release note stays
readable. The release jobs use the section as the release description.
"""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404 - fixed git argv, no shell
import sys
from collections.abc import Sequence
from dataclasses import dataclass

SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?: (?P<text>.+)$")
SECTIONS = (
    ("breaking", "Breaking changes"),
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("perf", "Performance"),
    ("maintenance", "Maintenance"),
)
MAINTENANCE = {"docs", "test", "ci", "build", "chore", "style", "refactor", "revert"}
TAG = re.compile(r"^v\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout  # noqa: S603, S607


def latest_tag(to: str) -> str | None:
    try:
        tags = git("tag", "--merged", to, "--sort=-v:refname").split()
    except subprocess.CalledProcessError:
        return None
    return next((t for t in tags if TAG.match(t)), None)


def commits(start: str | None, end: str) -> list[Commit]:
    rng = f"{start}..{end}" if start else end
    raw = git("log", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e", rng)
    out = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, subject, body = [*record.split("\x1f"), "", ""][:3]
        out.append(Commit(sha, subject.strip(), body))
    return out


def section_of(commit: Commit) -> tuple[str, str]:
    """(section, line) for one commit."""
    m = SUBJECT.match(commit.subject)
    if m is None:
        return "maintenance", commit.subject
    scope = f"**{m['scope']}**: " if m["scope"] else ""
    line = f"{scope}{m['text']}"
    if m["bang"] or "BREAKING CHANGE:" in commit.body:
        return "breaking", line
    kind = m["type"]
    if kind in {"feat", "fix", "perf"}:
        return kind, line
    return "maintenance", f"{kind}: {line}"


def render(items: Sequence[Commit], version: str, date: str) -> str:
    grouped: dict[str, list[str]] = {key: [] for key, _ in SECTIONS}
    for commit in items:
        section, line = section_of(commit)
        grouped[section].append(f"- {line} ({commit.sha[:8]})")
    lines = [f"## {version} — {date}", ""]
    for key, title in SECTIONS:
        if grouped[key]:
            lines += [f"### {title}", "", *grouped[key], ""]
    if len(lines) == 2:
        lines += ["No changes.", ""]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", default="HEAD")
    parser.add_argument("--version", default="Unreleased")
    args = parser.parse_args(argv)
    start = args.start or latest_tag(args.to)
    date = git("log", "-1", "--format=%cs", args.to).strip()
    sys.stdout.write(render(commits(start, args.to), args.version, date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
