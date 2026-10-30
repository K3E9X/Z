"""Gate dependency scanner reports on the docs/13 rule: high/critical findings without an accepted exception fail.

Stdlib only. Usage:

    vuln_gate.py osv REPORT.json [--exceptions security/exceptions.yaml] [--min-score 7.0]
    vuln_gate.py pip-audit REPORT.json [--exceptions security/exceptions.yaml]

- osv: osv-scanner `--format json`. A group fails when its max CVSS score is >= --min-score, or when it has
  no score at all (fail closed: unknown severity is treated as high). Exceptions apply per ecosystem
  (`deps:pypi`, `deps:npm`) and match the vulnerability id or any alias.
- pip-audit: `pip-audit --format json`. It carries no severity, so a vulnerability fails when a fixed version
  exists (an upgrade is available); the others are listed as warnings and left to the osv gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_exceptions import active, check

SCOPES = {"pypi": "deps:pypi", "npm": "deps:npm"}


def _excepted(exceptions_file: Path, scope: str) -> set[str]:
    return {e["id"] for e in active(exceptions_file, scope)}


def osv(report: dict[str, Any], exceptions_file: Path, min_score: float = 7.0) -> tuple[list[str], list[str]]:
    """(failures, warnings) for an osv-scanner JSON report."""
    failures: list[str] = []
    warnings: list[str] = []
    cache: dict[str, set[str]] = {}
    for result in report.get("results") or []:
        source = (result.get("source") or {}).get("path", "?")
        for entry in result.get("packages") or []:
            package = entry.get("package") or {}
            ecosystem = str(package.get("ecosystem", "")).lower()
            scope = SCOPES.get(ecosystem, f"deps:{ecosystem}")
            if scope not in cache:
                cache[scope] = _excepted(exceptions_file, scope) if scope in SCOPES.values() else set()
            label = f"{Path(source).name}: {package.get('name')} {package.get('version')}"
            for group in entry.get("groups") or []:
                ids = {*group.get("ids", []), *group.get("aliases", [])}
                name = ", ".join(sorted(group.get("ids", []))) or "unknown id"
                raw = str(group.get("max_severity") or "").strip()
                try:
                    score: float | None = float(raw)
                except ValueError:
                    score = None
                severity = f"score {raw}" if score is not None else "no score (treated as high)"
                line = f"{label}: {name} ({severity})"
                if ids & cache[scope]:
                    warnings.append(f"{line}: accepted exception")
                elif score is None or score >= min_score:
                    failures.append(line)
                else:
                    warnings.append(line)
    return failures, warnings


def pip_audit(report: dict[str, Any], exceptions_file: Path) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    excepted = _excepted(exceptions_file, "deps:pypi")
    for dependency in report.get("dependencies") or []:
        for vuln in dependency.get("vulns") or []:
            ids = {vuln.get("id", ""), *vuln.get("aliases", [])}
            fixes = vuln.get("fix_versions") or []
            line = f"{dependency.get('name')} {dependency.get('version')}: {vuln.get('id')}"
            if ids & excepted:
                warnings.append(f"{line}: accepted exception")
            elif fixes:
                failures.append(f"{line} (fixed in {', '.join(fixes)})")
            else:
                warnings.append(f"{line} (no fix yet)")
    return failures, warnings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=["osv", "pip-audit"])
    parser.add_argument("report", type=Path)
    parser.add_argument("--exceptions", type=Path, default=Path("security/exceptions.yaml"))
    parser.add_argument("--min-score", type=float, default=7.0)
    args = parser.parse_args(argv)
    problems = check(args.exceptions)
    if problems:
        print("FAIL security exceptions are invalid:", *problems, sep="\n  ")
        return 1
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL cannot read {args.report}: {exc}")
        return 1
    if not isinstance(report, dict):
        print(f"FAIL {args.report}: not a JSON object")
        return 1
    if args.kind == "osv":
        failures, warnings = osv(report, args.exceptions, args.min_score)
    else:
        failures, warnings = pip_audit(report, args.exceptions)
    for line in warnings:
        print(f"warn {line}")
    for line in failures:
        print(f"FAIL {line}")
    print(f"{args.kind}:", "ok" if not failures else f"{len(failures)} blocking finding(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
