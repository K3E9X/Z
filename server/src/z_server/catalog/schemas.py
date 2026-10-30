"""Resolve output schema paths of playbook steps (relative to the playbook's pack, or `<pack>/...`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def resolve(packs_root: Path, pack: str | None, path: str) -> Path | None:
    root = packs_root.resolve()
    candidates = [root / pack / path] if pack else []
    candidates.append(root / path)
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.is_relative_to(root) and resolved.suffix == ".json":
            return resolved
    return None


def load(packs_root: Path, pack: str | None, path: str) -> dict[str, Any]:
    found = resolve(packs_root, pack, path)
    if found is None:
        raise FileNotFoundError(path)
    data: dict[str, Any] = json.loads(found.read_text(encoding="utf-8"))
    return data
