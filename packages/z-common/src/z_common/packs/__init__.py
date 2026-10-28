"""Pack discovery and loading (raw documents, validated against the contracts)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from z_common.contracts import errors

FRONT_MATTER_DELIMITER = "---"


@dataclass
class Document:
    """One manifest file of a pack."""

    path: Path
    data: Any
    problems: list[str] = field(default_factory=list)


@dataclass
class Skill:
    path: Path
    front_matter: dict[str, Any]
    body: str
    problems: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return str(self.front_matter.get("name", self.path.parent.name))


@dataclass
class Pack:
    root: Path
    manifest: Document
    agents: list[Document] = field(default_factory=list)
    playbooks: list[Document] = field(default_factory=list)
    connectors: list[Document] = field(default_factory=list)
    objectives: list[Document] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    evals: dict[str, list[tuple[int, Any]]] = field(default_factory=dict)
    eval_problems: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        data = self.manifest.data
        return str(data.get("name")) if isinstance(data, dict) else self.root.name


def parse_skill_md(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []
    front: dict[str, Any] = {}
    body = text
    lines = text.splitlines()
    if lines and lines[0].strip() == FRONT_MATTER_DELIMITER:
        try:
            end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == FRONT_MATTER_DELIMITER)
        except StopIteration:
            problems.append("front matter is not closed with ---")
        else:
            loaded = yaml.safe_load("\n".join(lines[1:end])) or {}
            if isinstance(loaded, dict):
                front = loaded
            else:
                problems.append("front matter is not a mapping")
            body = "\n".join(lines[end + 1 :]).lstrip("\n")
    else:
        problems.append("missing YAML front matter")
    problems += errors("skill-frontmatter.schema.json", front)
    if front.get("name") and front["name"] != path.parent.name:
        problems.append(f"name {front['name']!r} does not match directory {path.parent.name!r}")
    if not body.strip():
        problems.append("empty instructions")
    return Skill(path=path, front_matter=front, body=body, problems=problems)


def _load_yaml(path: Path) -> Document:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return Document(path, None, [f"invalid YAML: {exc}"])
    return Document(path, data)


def _validated(path: Path, schema: str) -> Document:
    doc = _load_yaml(path)
    if not doc.problems:
        doc.problems = errors(schema, doc.data)
    return doc


def load_pack(root: Path) -> Pack:
    manifest_path = root / "pack.yaml"
    manifest = (
        _validated(manifest_path, "pack.schema.json")
        if manifest_path.is_file()
        else Document(manifest_path, None, ["missing pack.yaml"])
    )
    pack = Pack(root=root, manifest=manifest)
    for path in sorted((root / "agents").glob("*.yaml")):
        pack.agents.append(_validated(path, "agent.schema.json"))
    for path in sorted((root / "playbooks").glob("*.yaml")):
        pack.playbooks.append(_validated(path, "playbook.schema.json"))
    for path in sorted((root / "connectors").glob("*.yaml")):
        pack.connectors.append(_validated(path, "connector.schema.json"))
    for path in sorted((root / "objectives").glob("*.yaml")):
        doc = _load_yaml(path)
        if not doc.problems:
            items = doc.data if isinstance(doc.data, list) else [doc.data]
            for index, item in enumerate(items):
                doc.problems += [f"[{index}] {e}" for e in errors("objective.schema.json", item)]
        pack.objectives.append(doc)
    for path in sorted((root / "skills").glob("*/SKILL.md")):
        pack.skills.append(parse_skill_md(path))
    for path in sorted((root / "evals").glob("*.jsonl")):
        cases: list[tuple[int, Any]] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                pack.eval_problems.append(f"{path.name}:{number}: invalid JSON: {exc}")
                continue
            pack.eval_problems += [f"{path.name}:{number}: {e}" for e in errors("eval-case.schema.json", case)]
            cases.append((number, case))
        pack.evals[str(path.relative_to(root))] = cases
    return pack


def discover(packs_root: Path) -> list[Pack]:
    """Load every pack (a directory with pack.yaml) directly under `packs_root`."""
    return [load_pack(p) for p in sorted(packs_root.iterdir()) if (p / "pack.yaml").is_file()]
