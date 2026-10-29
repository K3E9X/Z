"""Security exceptions (docs/13 §2): validate `security/exceptions.yaml` and hand the active ids to scanners.

Stdlib only, so it runs before anything is installed. Usage:

    check_exceptions.py [--today YYYY-MM-DD] [FILE]          validate; exit 1 on a malformed or expired entry
    check_exceptions.py ids SCOPE [--format F] [FILE]          print the active ids that apply to SCOPE

Formats for `ids`: `lines` (one id per line: trivy --ignorefile, pip-audit/pnpm/dockle/semgrep flags) and
`osv-toml` (an osv-scanner config). `ids` validates the file first and prints nothing if it is invalid.

Scopes: `image:<name>` (or `image:*`), `deps:pypi`, `deps:npm`, `deps:*`, `iac` (trivy config), `sast`
(semgrep/bandit rule ids), `*`. Secrets have no exceptions: a leaked secret is rotated, not accepted.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simple_yaml import YamlSubsetError, load

REQUIRED = ("id", "scope", "reason", "owner", "expires")
MAX_HORIZON = timedelta(days=366)  # an exception is a dated debt, not a permanent waiver
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_SCOPE = re.compile(r"^(\*|iac|sast|deps:(pypi|npm|\*)|image:(\*|[a-z0-9][a-z0-9._-]*))$")


def _today() -> date:
    return datetime.now(UTC).date()


def _entries(path: Path) -> tuple[list[dict[str, object]], list[str]]:
    try:
        data = load(path.read_text(encoding="utf-8"))
    except (OSError, YamlSubsetError) as exc:
        return [], [f"{path}: {exc}"]
    if data is None:
        return [], []
    if not isinstance(data, dict) or set(data) - {"exceptions"}:
        return [], [f"{path}: the only top-level key is `exceptions`"]
    items = data.get("exceptions") or []
    if not isinstance(items, list):
        return [], [f"{path}: `exceptions` must be a list"]
    entries: list[dict[str, object]] = []
    problems: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f"exception #{index}: must be a mapping")
            continue
        entries.append(dict(item))
    return entries, problems


def check(path: Path, today: date | None = None) -> list[str]:
    """Every problem in the file: malformed, unknown scope, expired, too far in the future, duplicated."""
    today = today or _today()
    entries, problems = _entries(path)
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(entries):
        missing = [key for key in REQUIRED if not item.get(key)]
        if missing:
            problems.append(f"exception #{index}: missing {', '.join(missing)}")
            continue
        unknown = sorted(set(item) - set(REQUIRED))
        values = {key: item[key] for key in REQUIRED}
        if unknown or not all(isinstance(value, str) for value in values.values()):
            problems.append(f"exception #{index}: unknown keys {unknown} or non-text values")
            continue
        ident, scope, expires_text = str(values["id"]), str(values["scope"]), str(values["expires"])
        label = f"{ident} ({scope})"
        if not _ID.match(ident):
            problems.append(f"{label}: invalid id")
        if not _SCOPE.match(scope):
            problems.append(f"{label}: unknown scope (image:<name>, deps:pypi, deps:npm, iac, sast, *)")
        if (ident, scope) in seen:
            problems.append(f"{label}: duplicate")
        seen.add((ident, scope))
        try:
            expires = date.fromisoformat(expires_text)
        except ValueError:
            problems.append(f"{label}: expires must be YYYY-MM-DD, got {expires_text!r}")
            continue
        if expires < today:
            problems.append(f"{label}: expired on {expires}")
        elif expires > today + MAX_HORIZON:
            problems.append(f"{label}: expires {expires}, more than {MAX_HORIZON.days} days ahead")
    return problems


def active(path: Path, scope: str, today: date | None = None) -> list[dict[str, str]]:
    """Valid, unexpired exceptions whose scope covers `scope` (an exception for `image:*` covers `image:z-ui`)."""
    today = today or _today()
    entries, _ = _entries(path)
    found = []
    for item in entries:
        exception_scope = str(item.get("scope", ""))
        if fnmatch.fnmatchcase(scope, exception_scope) and date.fromisoformat(str(item["expires"])) >= today:
            found.append({key: str(item[key]) for key in REQUIRED})
    return found


def osv_toml(exceptions: list[dict[str, str]]) -> str:
    def quoted(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

    blocks = [
        f"[[IgnoredVulns]]\nid = {quoted(e['id'])}\nignoreUntil = {e['expires']}T23:59:59Z\n"
        f"reason = {quoted(e['reason'] + ' (owner ' + e['owner'] + ')')}\n"
        for e in exceptions
    ]
    return "\n".join(blocks)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["ids"]:
        parser = argparse.ArgumentParser(prog="check_exceptions.py ids")
        parser.add_argument("scope")
        parser.add_argument("file", nargs="?", default="security/exceptions.yaml")
        parser.add_argument("--format", choices=["lines", "osv-toml"], default="lines")
        ids_args = parser.parse_args(args[1:])
        path = Path(ids_args.file)
        problems = check(path)
        if problems:
            for problem in problems:
                print(f"FAIL {problem}", file=sys.stderr)
            return 1
        found = active(path, ids_args.scope)
        if ids_args.format == "osv-toml":
            sys.stdout.write(osv_toml(found))
        else:
            sys.stdout.write("".join(f"{e['id']}\n" for e in found))
        return 0
    parser = argparse.ArgumentParser(prog="check_exceptions.py", description=__doc__.splitlines()[0])
    parser.add_argument("file", nargs="?", default="security/exceptions.yaml")
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    check_args = parser.parse_args(args)
    problems = check(Path(check_args.file), today=check_args.today)
    for problem in problems:
        print(f"FAIL {problem}")
    print("exceptions:", "ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
