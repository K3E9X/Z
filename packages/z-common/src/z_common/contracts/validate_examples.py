"""Validate every document under contracts/examples/<schema-stem>/ against its schema.

Run: `uv run python -m z_common.contracts.validate_examples`. Files named `invalid-*` must FAIL.
Exit code 0 when every example behaves as expected.
"""

from __future__ import annotations

import sys
from pathlib import Path

from z_common.contracts import SCHEMA_FILES, contracts_dir, errors, load_document


def check(root: Path) -> list[str]:
    failures: list[str] = []
    examples = root / "examples"
    seen = 0
    for schema in SCHEMA_FILES:
        stem = schema.removesuffix(".schema.json")
        folder = examples / stem
        if stem == "common":
            continue
        files = sorted(p for p in folder.glob("*") if p.suffix in {".json", ".yaml", ".yml"}) if folder.is_dir() else []
        if not files:
            failures.append(f"{schema}: no examples in {folder.relative_to(root)}")
            continue
        for path in files:
            seen += 1
            problems = errors(schema, load_document(path))
            expect_invalid = path.name.startswith("invalid-")
            if expect_invalid and not problems:
                failures.append(f"{path.relative_to(root)}: expected to be INVALID but passed")
            elif not expect_invalid and problems:
                failures.append(f"{path.relative_to(root)}: " + "; ".join(problems))
    if seen == 0:
        failures.append("no examples found")
    return failures


def main() -> int:
    root = contracts_dir()
    failures = check(root)
    for failure in failures:
        print(f"FAIL {failure}")
    print("examples:", "ok" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
