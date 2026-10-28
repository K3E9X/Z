"""Fail when a security exception is malformed or expired (docs/13 §2). Usage: check_exceptions.py security/exceptions.yaml"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

REQUIRED = ("id", "scope", "reason", "owner", "expires")


def check(path: Path, today: date | None = None) -> list[str]:
    today = today or datetime.now(UTC).date()
    data = yaml.safe_load(path.read_text()) or {}
    problems = []
    for index, item in enumerate(data.get("exceptions") or []):
        missing = [k for k in REQUIRED if not item.get(k)]
        if missing:
            problems.append(f"exception #{index}: missing {', '.join(missing)}")
            continue
        expires = item["expires"] if isinstance(item["expires"], date) else date.fromisoformat(str(item["expires"]))
        if expires < today:
            problems.append(f"{item['id']} ({item['scope']}): expired on {expires}")
    return problems


if __name__ == "__main__":
    found = check(Path(sys.argv[1] if len(sys.argv) > 1 else "security/exceptions.yaml"))
    for problem in found:
        print(f"FAIL {problem}")
    print("exceptions:", "ok" if not found else f"{len(found)} problem(s)")
    sys.exit(1 if found else 0)
