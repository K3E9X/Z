"""License gate (docs/13 §2): every installed third-party package must carry a license the policy allows.

Stdlib only. Reads what is really installed, not what a lockfile claims:

    check_licenses.py --python SITE_PACKAGES [--node NODE_MODULES] [--policy security/licenses.yaml]

- Python: `*.dist-info/METADATA` (`License-Expression`, else `License` when it is a short identifier, else
  the `License ::` classifiers).
- Node (pnpm layout): `node_modules/.pnpm/*/node_modules/**/package.json` (`license`, or legacy `licenses`).

A package passes when its license expression is allowed: `A OR B` needs one allowed side, `A AND B` both.
Denied licenses (strong copyleft, source-available, non-commercial) fail even inside an OR with an unknown.
Unknown or missing metadata fails unless the policy lists the package with the license it was checked under.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from email.parser import HeaderParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simple_yaml import YamlSubsetError, load

CLASSIFIERS = {
    "MIT License": "MIT",
    "BSD License": "BSD-3-Clause",
    "Apache Software License": "Apache-2.0",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    "GNU Lesser General Public License v3 or later (LGPLv3+)": "LGPL-3.0-or-later",
    "GNU Lesser General Public License v2 or later (LGPLv2+)": "LGPL-2.1-or-later",
    "GNU Library or Lesser General Public License (LGPL)": "LGPL-2.1-or-later",
    "GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    "GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "GNU Affero General Public License v3": "AGPL-3.0-only",
    "The Unlicense (Unlicense)": "Unlicense",
    "Historical Permission Notice and Disclaimer (HPND)": "HPND",
    "zlib/libpng License": "Zlib",
}
# Common non-SPDX spellings found in package metadata.
ALIASES = {
    "apache 2.0": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "bsd": "BSD-3-Clause",
    "new bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "3-clause bsd": "BSD-3-Clause",
    "mit license": "MIT",
    "the mit license": "MIT",
    "expat": "MIT",
    "psf": "PSF-2.0",
    "psfl": "PSF-2.0",
    "python-2.0": "PSF-2.0",
    "mpl 2.0": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "isc license": "ISC",
}
_TOKEN = re.compile(r"\(|\)|\bAND\b|\bOR\b|\bWITH\b|[^\s()]+", re.IGNORECASE)


@dataclass(frozen=True)
class Package:
    ecosystem: str
    name: str
    version: str
    license: str | None


@dataclass(frozen=True)
class Policy:
    allow: frozenset[str]
    deny_patterns: tuple[re.Pattern[str], ...]
    packages: dict[tuple[str, str], str]  # (ecosystem, name) -> the license it was reviewed under
    ignore: frozenset[tuple[str, str]]  # our own workspace packages


def canonical(ecosystem: str, name: str) -> str:
    """PEP 503 names for PyPI (`func_timeout` is `func-timeout`); npm names are already canonical."""
    return re.sub(r"[-_.]+", "-", name).lower() if ecosystem == "pypi" else name.lower()


def load_policy(path: Path) -> Policy:
    data = load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: expected a mapping")
    allow = data.get("allow") or []
    deny = data.get("deny") or []
    if not isinstance(allow, list) or not isinstance(deny, list):
        raise SystemExit(f"{path}: allow and deny are lists")
    packages: dict[tuple[str, str], str] = {}
    for entry in data.get("packages") or []:
        if not isinstance(entry, dict) or not all(entry.get(k) for k in ("ecosystem", "name", "license", "reason")):
            raise SystemExit(f"{path}: every package entry has ecosystem, name, license and reason")
        packages[(str(entry["ecosystem"]), canonical(str(entry["ecosystem"]), str(entry["name"])))] = str(
            entry["license"]
        )
    ignore = set()
    for entry in data.get("ignore") or []:
        if not isinstance(entry, dict) or not entry.get("ecosystem") or not entry.get("name"):
            raise SystemExit(f"{path}: every ignore entry has ecosystem and name")
        ignore.add((str(entry["ecosystem"]), canonical(str(entry["ecosystem"]), str(entry["name"]))))
    return Policy(
        frozenset(str(a) for a in allow),
        tuple(re.compile(str(p), re.IGNORECASE) for p in deny),
        packages,
        frozenset(ignore),
    )


# ---------------------------------------------------------------- discovery


def python_packages(site: Path) -> Iterator[Package]:
    for meta in sorted(site.glob("*.dist-info/METADATA")):
        headers = HeaderParser().parsestr(meta.read_text(encoding="utf-8", errors="replace"))
        name, version = headers.get("Name", meta.parent.name), headers.get("Version", "")
        expression = (headers.get("License-Expression") or "").strip()
        if not expression:
            declared = (headers.get("License") or "").strip()
            if declared and "\n" not in declared and len(declared) <= 80:
                expression = declared
        if not expression:
            found = [
                CLASSIFIERS[c.split("::")[-1].strip()]
                for c in headers.get_all("Classifier") or []
                if c.startswith("License ::") and c.split("::")[-1].strip() in CLASSIFIERS
            ]
            expression = " OR ".join(dict.fromkeys(found))
        yield Package("pypi", name, version, expression or None)


def node_packages(node_modules: Path) -> Iterator[Package]:
    seen: set[tuple[str, str]] = set()
    for manifest in sorted((node_modules / ".pnpm").glob("*/node_modules/**/package.json")):
        if "/node_modules/" in str(manifest.relative_to(node_modules / ".pnpm")).split("/node_modules/", 1)[1]:
            continue  # nested copies inside a package's own node_modules are other entries already
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("name") or not data.get("version"):
            continue
        key = (str(data["name"]), str(data["version"]))
        if key in seen:
            continue
        seen.add(key)
        raw = data.get("license")
        if isinstance(raw, dict):
            raw = raw.get("type")
        if raw is None and isinstance(data.get("licenses"), list):
            raw = " OR ".join(str(x.get("type")) for x in data["licenses"] if isinstance(x, dict) and x.get("type"))
        yield Package("npm", key[0], key[1], str(raw) if raw else None)


# ---------------------------------------------------------------- evaluation


def normalise(license_id: str) -> str:
    plain = license_id.strip().strip('"').strip()
    return ALIASES.get(plain.lower(), plain)


def verdict(expression: str, policy: Policy) -> str:
    """'allowed', 'denied' or 'unknown' for an SPDX-like expression."""
    text = expression.replace("/", " OR ")
    # names with spaces or commas ("Apache License, Version 2.0") become one token before tokenizing
    for alias in sorted((a for a in ALIASES if " " in a or "," in a), key=len, reverse=True):
        text = re.sub(rf"(?i)(?<![\w.-]){re.escape(alias)}(?![\w.-])", ALIASES[alias], text)
    tokens = _TOKEN.findall(text)

    pos = 0

    def atom() -> str:
        nonlocal pos
        if pos >= len(tokens):
            return "unknown"
        word = tokens[pos]
        pos += 1
        if word == "(":
            value = disjunction()
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
            return value
        if pos + 1 < len(tokens) and tokens[pos].upper() == "WITH":
            pos += 2  # an exception (e.g. LLVM-exception) only relaxes the license
        name = normalise(word)
        if any(p.search(name) for p in policy.deny_patterns):
            return "denied"
        return "allowed" if name in policy.allow else "unknown"

    def conjunction() -> str:
        nonlocal pos
        values = [atom()]
        while pos < len(tokens) and tokens[pos].upper() == "AND":
            pos += 1
            values.append(atom())
        if "denied" in values:
            return "denied"
        return "allowed" if all(v == "allowed" for v in values) else "unknown"

    def disjunction() -> str:
        nonlocal pos
        values = [conjunction()]
        while pos < len(tokens) and tokens[pos].upper() == "OR":
            pos += 1
            values.append(conjunction())
        if "allowed" in values:
            return "allowed"
        return "denied" if "denied" in values else "unknown"

    return disjunction()


def check(packages: list[Package], policy: Policy) -> list[str]:
    problems = []
    for pkg in packages:
        key = (pkg.ecosystem, canonical(pkg.ecosystem, pkg.name))
        if key in policy.ignore:
            continue
        reviewed = policy.packages.get(key)
        expression = reviewed or pkg.license
        if not expression:
            problems.append(f"{pkg.ecosystem} {pkg.name} {pkg.version}: no license metadata (review it in the policy)")
            continue
        result = verdict(expression, policy)
        if result != "allowed":
            problems.append(f"{pkg.ecosystem} {pkg.name} {pkg.version}: {expression!r} is {result}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", type=Path, action="append", default=[], help="a site-packages directory")
    parser.add_argument("--node", type=Path, action="append", default=[], help="a pnpm node_modules directory")
    parser.add_argument("--policy", type=Path, default=Path("security/licenses.yaml"))
    args = parser.parse_args(argv)
    try:
        policy = load_policy(args.policy)
    except YamlSubsetError as exc:
        print(f"{args.policy}: {exc}", file=sys.stderr)
        return 2
    packages = [p for site in args.python for p in python_packages(site)]
    packages += [p for nm in args.node for p in node_packages(nm)]
    if not packages:
        print("no installed packages found: check the paths", file=sys.stderr)
        return 2
    problems = check(packages, policy)
    for line in problems:
        print(line)
    print(f"licenses: {len(packages)} packages checked, {len(problems)} problem(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
