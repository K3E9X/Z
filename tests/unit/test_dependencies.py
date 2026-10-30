"""Every third-party module a package imports directly is declared in that package's pyproject.toml.

A dependency that only arrives transitively works in the development environment and breaks in the image
(the compose e2e found python-multipart that way)."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = {
    "server": "server/src/z_server",
    "runner": "runner/src/z_runner",
    "cli": "cli/src/z_cli",
    "z-common": "packages/z-common/src/z_common",
}
# import name -> distribution name, when they differ
DIST = {
    "jwt": "pyjwt",
    "argon2": "argon2-cffi",
    "prometheus_client": "prometheus-client",
    "multipart": "python-multipart",
    "python_multipart": "python-multipart",
    "yaml": "pyyaml",
    "openhands": "openhands-sdk",
    "docx": "python-docx",
    "pptx": "python-pptx",
}
FIRST_PARTY = {"z_common", "z_server", "z_runner", "z_cli"}


def imports(src: Path) -> set[str]:
    found: set[str] = set()
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return {m for m in found if m not in sys.stdlib_module_names and m not in FIRST_PARTY and m != "__future__"}


def declared(pyproject: Path) -> set[str]:
    project = tomllib.loads(pyproject.read_text())["project"]
    names = list(project.get("dependencies", []))
    for extra in (project.get("optional-dependencies") or {}).values():
        names += extra
    return {re.split(r"[<>=!~\[; ]", n, maxsplit=1)[0].lower().replace("_", "-") for n in names}


@pytest.mark.parametrize("package", sorted(PACKAGES))
def test_direct_imports_are_declared(package: str) -> None:
    src = ROOT / PACKAGES[package]
    pyproject = next(p / "pyproject.toml" for p in [src, *src.parents] if (p / "pyproject.toml").exists())
    deps = declared(pyproject)
    missing = sorted(m for m in imports(src) if DIST.get(m, m).lower().replace("_", "-") not in deps)
    assert not missing, f"{pyproject.relative_to(ROOT)} does not declare: {missing}"
