"""Fail when the repository pins a blocked version (`security/blocked-versions.yaml`, docs/13 §3).

Stdlib only, so it runs before anything is installed. Checked:

- Python: `uv.lock` and the hashed scanner requirements (`ci/scripts/requirements/*.txt`);
- npm: `ui/pnpm-lock.yaml`;
- scanner binaries: `ci/scripts/versions.lock`;
- container images: `image:` keys, Helm `repository:`/`tag:` pairs, Dockerfile `FROM` lines and `*_IMAGE=` defaults in `deploy/`, `tests/e2e/`,
  `.github/workflows/` and `.gitlab-ci.yml` (an image matches a rule when its last path component is the rule's
  short name and its tag carries a blocked version, e.g. `litellm/litellm:main-v1.82.7`).

Usage: check_blocked.py [--root DIR] [--lock FILE]   (--lock: check only that versions.lock; used by
install-scanners.sh before it downloads anything)
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simple_yaml import YamlSubsetError, load

ROOT = Path(__file__).resolve().parents[2]
ECOSYSTEMS = {"pypi", "npm", "github-release", "image"}
IMAGE_GLOBS = (
    "deploy/**/*.yml",
    "deploy/**/*.yaml",
    "deploy/**/Dockerfile*",
    "tests/e2e/*.yml",
    "tests/e2e/Dockerfile*",
    ".github/workflows/*.yml",
    ".gitlab-ci.yml",
)
_IMAGE_REF = re.compile(
    r"""(?:^|\s)(?:image:|FROM|ARG\s+\w*IMAGE\w*=)\s*['"]?(?P<repo>[\w.-]+(?::\d+)?(?:/[\w.-]+)*):(?P<tag>[\w][\w.-]*)""",
    re.MULTILINE,
)
# Helm values style: `repository: docker.io/litellm/litellm` with `tag: v1.105.0` on the next line.
_HELM_REF = re.compile(
    r"""^\s*repository:\s*['"]?(?P<repo>[\w.-]+(?::\d+)?(?:/[\w.-]+)*)['"]?\s*\n\s*tag:\s*['"]?(?P<tag>[\w][\w.-]*)""",
    re.MULTILINE,
)
_VERSION = re.compile(r"\d+(?:\.\d+)+")
_PNPM_KEY = re.compile(r"""^ {2}['"]?/?(?P<name>@?[^@\s'"(/]+(?:/[^@\s'"(]+)?)@(?P<version>[^('":\s]+)""", re.MULTILINE)
_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)", re.MULTILINE)


@dataclass(frozen=True)
class Rule:
    ecosystem: str
    name: str
    versions: frozenset[str]
    reason: str

    @property
    def short(self) -> str:
        return self.name.rsplit("/", 1)[-1].lower()


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def rules(root: Path) -> list[Rule]:
    path = root / "security/blocked-versions.yaml"
    data = load(path.read_text(encoding="utf-8"))
    entries = data.get("blocked") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise YamlSubsetError(f"{path}: expected a `blocked` list")
    out = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise YamlSubsetError(f"{path}: entry #{index} must be a mapping")
        ecosystem, name, reason, versions = (item.get(k) for k in ("ecosystem", "name", "reason", "versions"))
        if ecosystem not in ECOSYSTEMS or not isinstance(name, str) or not name or not isinstance(reason, str):
            raise YamlSubsetError(
                f"{path}: entry #{index} needs ecosystem ({'|'.join(sorted(ECOSYSTEMS))}), name, reason"
            )
        if not isinstance(versions, list) or not versions or not all(isinstance(v, str) and v for v in versions):
            raise YamlSubsetError(f"{path}: entry #{index} needs a non-empty list of quoted versions")
        out.append(Rule(str(ecosystem), name, frozenset(str(v).removeprefix("v") for v in versions), reason))
    return out


def _uv_lock(root: Path) -> Iterable[tuple[str, str, str]]:
    path = root / "uv.lock"
    if path.exists():
        for package in tomllib.loads(path.read_text(encoding="utf-8")).get("package", []):
            yield "uv.lock", str(package.get("name", "")), str(package.get("version", ""))


def _requirements(root: Path) -> Iterable[tuple[str, str, str]]:
    for path in sorted(root.glob("ci/scripts/requirements/*.txt")):
        for match in _REQUIREMENT.finditer(path.read_text(encoding="utf-8")):
            yield str(path.relative_to(root)), match["name"], match["version"]


def _pnpm_lock(root: Path) -> Iterable[tuple[str, str, str]]:
    path = root / "ui/pnpm-lock.yaml"
    if path.exists():
        for match in _PNPM_KEY.finditer(path.read_text(encoding="utf-8")):
            yield "ui/pnpm-lock.yaml", match["name"], match["version"]


def versions_lock(path: Path) -> Iterable[tuple[str, str]]:
    """(tool, version) pairs of a versions.lock (`name version arch sha256 kind url`)."""
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("#", 1)[0].split()
        if fields:
            yield fields[0], fields[1].removeprefix("v")


def _images(root: Path) -> Iterable[tuple[str, str, str]]:
    seen: set[Path] = set()
    for pattern in IMAGE_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in [*_IMAGE_REF.finditer(text), *_HELM_REF.finditer(text)]:
                yield str(path.relative_to(root)), match["repo"].lower(), match["tag"]


def check_lock(path: Path, root: Path | None = None) -> list[str]:
    blocked = [r for r in rules(root or ROOT) if r.ecosystem == "github-release"]
    return [
        f"{path.name}: {tool} {version} is blocked ({rule.reason})"
        for tool, version in versions_lock(path)
        for rule in blocked
        if tool.lower() == rule.short and version in rule.versions
    ]


def check(root: Path | None = None) -> list[str]:
    root = root or ROOT
    problems: list[str] = []
    all_rules = rules(root)
    python = [r for r in all_rules if r.ecosystem == "pypi"]
    for source, name, version in [*_uv_lock(root), *_requirements(root)]:
        problems += [
            f"{source}: {name} {version} is blocked ({rule.reason})"
            for rule in python
            if _normalize(name) == _normalize(rule.name) and version in rule.versions
        ]
    npm = [r for r in all_rules if r.ecosystem == "npm"]
    for source, name, version in _pnpm_lock(root):
        problems += [
            f"{source}: {name} {version} is blocked ({rule.reason})"
            for rule in npm
            if name.lower() == rule.name.lower() and version in rule.versions
        ]
    lock = root / "ci/scripts/versions.lock"
    if lock.exists():
        problems += [f"ci/scripts/{p}" for p in check_lock(lock, root)]
    for source, repo, tag in _images(root):
        short = repo.rsplit("/", 1)[-1]
        for rule in all_rules:
            hits = rule.versions.intersection(_VERSION.findall(tag))
            if short == rule.short and hits:
                version = sorted(hits)[0]
                problems.append(f"{source}: {repo}:{version} is blocked (tag {tag}; {rule.reason})")
    return sorted(set(problems))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when a blocked version is pinned.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--lock", type=Path, help="check only this versions.lock")
    args = parser.parse_args(argv)
    try:
        found = check_lock(args.lock, args.root) if args.lock else check(args.root)
    except (OSError, YamlSubsetError, tomllib.TOMLDecodeError, IndexError) as exc:
        print(f"FAIL cannot check blocked versions: {exc}")
        return 1
    for problem in found:
        print(f"FAIL {problem}")
    print("blocked versions:", "ok" if not found else f"{len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
