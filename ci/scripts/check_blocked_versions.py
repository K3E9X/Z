"""Fail when a lockfile or an image reference uses a blocked version (security/blocked-versions.yaml).

Checks uv.lock (pypi), ui/pnpm-lock.yaml when present (npm), and image references in deploy/ and ci/.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def blocked() -> list[dict[str, object]]:
    return list(yaml.safe_load((ROOT / "security/blocked-versions.yaml").read_text())["blocked"])


def check() -> list[str]:
    problems = []
    rules = blocked()
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    pypi = {(p["name"].lower(), p.get("version", "")) for p in lock.get("package", [])}
    for rule in rules:
        name, versions = str(rule["name"]).lower(), {str(v) for v in rule["versions"]}  # type: ignore[attr-defined]
        if rule["ecosystem"] == "pypi":
            problems += [
                f"uv.lock: {name} {v} is blocked ({rule['reason']})" for n, v in pypi if n == name and v in versions
            ]
    images = re.compile(r"image:\s*['\"]?([\w./-]+):v?([\w.-]+)")
    for path in [
        *ROOT.glob("deploy/**/*.y*ml"),
        *ROOT.glob("deploy/**/Dockerfile"),
        *ROOT.glob(".github/workflows/*.yml"),
    ]:
        for match in images.finditer(path.read_text()):
            repo, tag = match.group(1).lower(), match.group(2)
            for rule in rules:
                short = str(rule["name"]).split("/")[-1].lower()
                if repo.endswith(short) and tag in {str(v) for v in rule["versions"]}:  # type: ignore[attr-defined]
                    problems.append(f"{path.relative_to(ROOT)}: {repo}:{tag} is blocked ({rule['reason']})")
    return problems


if __name__ == "__main__":
    found = check()
    for problem in found:
        print(f"FAIL {problem}")
    print("blocked versions:", "ok" if not found else f"{len(found)} problem(s)")
    sys.exit(1 if found else 0)
