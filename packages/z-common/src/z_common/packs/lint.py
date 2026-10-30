"""Pack lint (BUILD phase A): cross-file rules the JSON Schemas cannot express.

Run: `uv run python -m z_common.packs.lint packs/`. Exit code 1 on any error (warnings do not fail).

Rules
- every manifest validates against its contract (schemas, skill front matter, eval JSONL);
- names are unique across packs: agents, handles, skills, playbooks, connectors;
- every agent skill exists; every agent and skill `evals` file exists (suites smaller than docs/08 §6 asks are
  warned); gateway tool refs name a known connector;
- write or destructive operations never appear in agent tools (globs only ever match reads);
- every playbook step agent is in the playbook team and exists;
- `depends_on`, `fanout.over`, `inputs_from` reference earlier steps; fan-out sources have an output schema
  (`steps.X.output.…`) or are fan-out steps (`steps.X.outputs`); output schema files exist;
- `writes` are full ids of a known connector, of class write or destructive, and that connector is in the
  playbook's `connectors`; `workspace: git_clone` has `repo`;
- connector rules: globs only for read; a rule may not lower the classifier floor (05 §3);
- eval checks: `json_schema` files exist; count/no_action operations are full ids.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from z_common.connectors.browser import connector_problems as browser_problems
from z_common.connectors.cli import connector_problems as cli_problems
from z_common.connectors.oauth import connector_problems as oauth_problems
from z_common.connectors.operations import name_problems
from z_common.connectors.queries import connector_problems as query_problems
from z_common.contracts import contracts_dir
from z_common.models.enums import OperationClass
from z_common.packs import Pack, discover
from z_common.packs.classify import ORDER, classify, glob_match, is_glob, raise_only

STEP_REF = re.compile(r"^steps\.([a-z0-9_-]+)\.(output(\.[A-Za-z0-9_]+)*|outputs)$")
PLACEHOLDER = re.compile(r"^<.*>$")
# what eval check paths address: the job result (contracts/eval-case.schema.json, z_server.evals.result_context)
RESULT_ROOTS = ("output", "report", "actions", "questions", "artifacts", "tool_calls", "diff")
MIN_AGENT_CASES, MIN_SKILL_CASES = 10, 5  # docs/08 §6: minimum suite sizes for seed agents and skills


def _suite_size(pack: Pack, rel: str, skill: str | None = None) -> int:
    """Cases of an eval file; for a skill, the cases marked with it (all cases when none is marked)."""
    cases = [c for _, c in pack.evals.get(rel, []) if isinstance(c, dict)]
    if skill is not None and any(c.get("skill") for c in cases):
        cases = [c for c in cases if c.get("skill") == skill]
    return len(cases)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")


@dataclass
class KnownOperation:
    connector: str
    local_id: str
    cls: OperationClass
    method: str | None = None

    @property
    def full_id(self) -> str:
        return f"{self.connector}.{self.local_id}"


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def connector_operations(manifest: dict[str, Any], pack_root: Path) -> list[KnownOperation]:
    """Operations statically known from a connector file (map, declared, local OpenAPI spec, exact rules)."""
    name = str(manifest["name"])
    found: dict[str, str | None] = {}
    method: str | None
    for local in (manifest.get("operation_map") or {}).values():
        found.setdefault(local, None)
    for declared in manifest.get("declared") or []:
        found[declared["id"]] = declared.get("method")
    spec = manifest.get("spec")
    if manifest.get("kind") == "openapi" and isinstance(spec, str) and not spec.startswith(("http", "$", "builtin:")):
        from z_common.connectors.openapi import operations_from_spec  # local import: optional for lint

        spec_path = (pack_root / "connectors" / spec).resolve()
        if spec_path.is_file():
            for local_id, method in operations_from_spec(
                spec_path, manifest.get("operation_ids") or "from_operation_id"
            ).items():
                found[local_id] = method
    for rule in (manifest.get("operations") or {}).get("rules") or []:
        if not is_glob(rule["match"]):
            found.setdefault(rule["match"], found.get(rule["match"]))
    rules = (manifest.get("operations") or {}).get("rules") or []
    result = []
    for local_id, method in found.items():
        floor = classify(local_id, method)
        rule_class = None
        for rule in rules:
            if rule["match"] == local_id or (
                is_glob(rule["match"]) and glob_match(rule["match"], local_id) and floor == OperationClass.read
            ):
                rule_class = OperationClass(rule["class"]) if rule.get("class") else None
                break
        result.append(KnownOperation(name, local_id, raise_only(floor, rule_class), method))
    return result


def spec_problems(manifest: dict[str, Any], pack_root: Path) -> list[str]:
    """openapi connectors: `builtin:` specs do not exist, and with a local spec every exact rule must name one of
    its operations (a rule for an operation the spec lacks would expose an id with no method or path)."""
    spec = manifest.get("spec")
    if manifest.get("kind") != "openapi" or not isinstance(spec, str):
        return []
    if spec.startswith("builtin:"):
        return [f"spec {spec!r}: there are no built-in specs; ship the OpenAPI file next to the connector"]
    if spec.startswith(("http", "$")):
        return []  # fetched when the connector is created; checked then
    path = (pack_root / "connectors" / spec).resolve()
    if not path.is_file():
        return [f"spec {spec!r} not found next to the connector"]
    from z_common.connectors.openapi import operations_from_spec

    in_spec = set(operations_from_spec(path, manifest.get("operation_ids") or "from_operation_id"))
    declared = {str(d["id"]) for d in manifest.get("declared") or []}
    rules = (manifest.get("operations") or {}).get("rules") or []
    return [
        f"rule {rule['match']!r} names no operation of {spec}"
        for rule in rules
        if not is_glob(rule["match"]) and rule["match"] not in in_spec | declared
    ]


def lint(packs: list[Pack], repo_root: Path) -> Report:
    report = Report()
    agents: dict[str, tuple[Pack, dict[str, Any]]] = {}
    handles: dict[str, str] = {}
    skills: dict[str, Pack] = {}
    playbooks: dict[str, Pack] = {}
    connectors: dict[str, tuple[Pack, dict[str, Any]]] = {}
    operations: dict[str, KnownOperation] = {}

    # 1. schema problems and inventory
    for pack in packs:
        where = _rel(pack.root, repo_root)
        for problem in pack.manifest.problems:
            report.error(f"{where}/pack.yaml", problem)
        if isinstance(pack.manifest.data, dict) and pack.manifest.data.get("name") != pack.root.name:
            report.error(f"{where}/pack.yaml", f"name must equal the directory name {pack.root.name!r}")
        for skill in pack.skills:
            for problem in skill.problems:
                report.error(_rel(skill.path, repo_root), problem)
            if skill.name in skills:
                report.error(
                    _rel(skill.path, repo_root),
                    f"skill {skill.name!r} already defined in pack {skills[skill.name].name}",
                )
            skills[skill.name] = pack
            suite = skill.front_matter.get("evals")
            if not suite:
                report.warn(_rel(skill.path, repo_root), "no eval suite")
            elif not (pack.root / str(suite)).is_file():
                report.error(_rel(skill.path, repo_root), f"eval file {suite} not found")
            elif (size := _suite_size(pack, str(suite), skill.name)) < MIN_SKILL_CASES:
                report.warn(_rel(skill.path, repo_root), f"eval suite has {size} cases, fewer than {MIN_SKILL_CASES}")
        for doc in pack.agents + pack.playbooks + pack.connectors + pack.objectives:
            for problem in doc.problems:
                report.error(_rel(doc.path, repo_root), problem)
        for problem in pack.eval_problems:
            report.error(f"{where}/evals", problem)
        for doc in pack.agents:
            if doc.problems or not isinstance(doc.data, dict):
                continue
            name, handle = doc.data["name"], doc.data["handle"]
            if doc.path.stem != name:
                report.error(_rel(doc.path, repo_root), f"file name must be {name}.yaml")
            if name in agents:
                report.error(
                    _rel(doc.path, repo_root), f"agent {name!r} already defined in pack {agents[name][0].name}"
                )
            if handle in handles:
                report.error(_rel(doc.path, repo_root), f"handle @{handle} already used by {handles[handle]}")
            agents[name] = (pack, doc.data)
            handles[handle] = name
        for doc in pack.playbooks:
            if doc.problems or not isinstance(doc.data, dict):
                continue
            if doc.data["name"] in playbooks:
                report.error(_rel(doc.path, repo_root), f"playbook {doc.data['name']!r} already defined")
            playbooks[doc.data["name"]] = pack
        for doc in pack.connectors:
            if doc.problems or not isinstance(doc.data, dict):
                continue
            cname = doc.data["name"]
            if cname in connectors:
                report.error(_rel(doc.path, repo_root), f"connector {cname!r} already defined")
            connectors[cname] = (pack, doc.data)
            _lint_connector_rules(doc.data, pack, _rel(doc.path, repo_root), report)
            problems = browser_problems(doc.data) + query_problems(doc.data) + cli_problems(doc.data)
            problems += oauth_problems(doc.data) + name_problems(doc.data)
            for problem in problems + spec_problems(doc.data, pack.root):
                report.error(_rel(doc.path, repo_root), problem)
            for known in connector_operations(doc.data, pack.root):
                operations[known.full_id] = known

    # 2. agents
    for name, (pack, agent) in agents.items():
        where = f"{_rel(pack.root, repo_root)}/agents/{name}.yaml"
        for skill in agent.get("skills", []):
            if skill not in skills:
                report.error(where, f"skill {skill!r} does not exist in any pack")
        if agent.get("evals") and not (pack.root / agent["evals"]).is_file():
            report.error(where, f"eval file {agent['evals']} not found")
        elif agent.get("evals") and (size := _suite_size(pack, agent["evals"])) < MIN_AGENT_CASES:
            report.warn(where, f"eval suite has {size} cases, fewer than {MIN_AGENT_CASES}")
        if not agent.get("evals"):
            report.warn(where, "no eval suite")
        for tool in agent.get("tools", []):
            if not tool.startswith("gateway:") or tool == "gateway:@read":
                continue
            pattern = tool.removeprefix("gateway:")
            connector = pattern.split(".", 1)[0]
            if connector not in connectors:
                report.error(where, f"tool {tool}: unknown connector {connector!r}")
                continue
            if is_glob(pattern):
                continue  # globs only ever expand to read operations (gateway and lint)
            known_tool = operations.get(pattern)
            if known_tool is None:
                report.warn(
                    where, f"tool {tool}: operation not declared statically by the connector (checked at runtime)"
                )
            elif known_tool.cls != OperationClass.read:
                report.error(
                    where, f"tool {tool}: {known_tool.cls.value} operation; writes are proposed, never listed as tools"
                )

    # 3. playbooks
    for pack in packs:
        for doc in pack.playbooks:
            if doc.problems or not isinstance(doc.data, dict):
                continue
            root = pack.root
            lint_playbook(
                doc.data,
                _rel(doc.path, repo_root),
                agents,
                connectors,
                operations,
                lambda path, root=root: (root / path).resolve().is_file(),  # type: ignore[misc]
                report,
            )

    # 4. objectives
    for pack in packs:
        for doc in pack.objectives:
            items = doc.data if isinstance(doc.data, list) else [doc.data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("agent") not in agents:
                    report.error(_rel(doc.path, repo_root), f"objective agent {item.get('agent')!r} does not exist")
                for playbook in item.get("allowed_playbooks", []):
                    if playbook not in playbooks:
                        report.error(_rel(doc.path, repo_root), f"allowed playbook {playbook!r} does not exist")

    # 5. evals
    for pack in packs:
        for file, cases in pack.evals.items():
            where = f"{_rel(pack.root, repo_root)}/{file}"
            for number, case in cases:
                for check in case.get("checks", []) if isinstance(case, dict) else []:
                    head = re.split(r"[.\[]", str(check.get("path", "output")), maxsplit=1)[0]
                    if head not in RESULT_ROOTS:
                        report.error(
                            f"{where}:{number}",
                            f"check path {check.get('path')!r} must start with one of {', '.join(RESULT_ROOTS)}",
                        )
                    if check.get("type") == "json_schema":
                        schema = str(check.get("schema", ""))
                        target = (
                            contracts_dir() / schema.removeprefix("contracts/")
                            if schema.startswith("contracts/")
                            else pack.root / schema
                        )
                        if not target.is_file():
                            report.error(f"{where}:{number}", f"json_schema {schema} not found")
                for fixture in (case.get("context_fixtures") or {}).values() if isinstance(case, dict) else []:
                    if (
                        isinstance(fixture, str)
                        and fixture.startswith("evals/fixtures/")
                        and not (pack.root / fixture).exists()
                    ):
                        report.error(f"{where}:{number}", f"fixture {fixture} not found")
    return report


def _lint_connector_rules(manifest: dict[str, Any], pack: Pack, where: str, report: Report) -> None:
    declared_methods = {d["id"]: d.get("method") for d in manifest.get("declared") or []}
    for rule in (manifest.get("operations") or {}).get("rules") or []:
        match, cls = rule["match"], rule.get("class")
        if is_glob(match):
            if cls and cls != "read":
                report.error(where, f"rule {match!r}: globs apply only to read operations, class {cls!r} not allowed")
            continue
        if not cls:
            continue
        floor = classify(match, declared_methods.get(match))
        if ORDER[OperationClass(cls)] < ORDER[floor]:
            report.error(where, f"rule {match!r}: class {cls!r} is lower than the platform floor {floor.value!r}")
    auth = manifest.get("auth") or {}
    read_auth, write_auth = auth.get("read"), auth.get("write")
    if isinstance(read_auth, dict) and read_auth.get("secret") and read_auth == write_auth:
        report.error(where, "read and write credentials must differ")


def lint_playbook(
    playbook: dict[str, Any],
    where: str,
    agents: Collection[str],
    connectors: Collection[str],
    operations: Mapping[str, KnownOperation],
    schema_exists: Callable[[str], bool],
    report: Report,
) -> None:
    """Cross-reference rules for one playbook. Used by pack lint and by the API on create/edit."""
    team = set(playbook.get("team", []))
    for member in team:
        if member not in agents:
            report.error(where, f"team member {member!r} does not exist")
    for connector in playbook.get("connectors", []):
        if connector not in connectors:
            report.error(where, f"connector {connector!r} does not exist")
    seen: dict[str, dict[str, Any]] = {}
    for step in playbook.get("steps", []):
        sid = step["id"]
        swhere = f"{where} step {sid}"
        if sid in seen:
            report.error(swhere, "duplicate step id")
        if step["agent"] not in team:
            report.error(swhere, f"agent {step['agent']!r} is not in the team")
        for dep in step.get("depends_on", []):
            if dep not in seen:
                report.error(swhere, f"depends_on {dep!r} is not an earlier step")
        for key in ("inputs_from",):
            if step.get(key):
                _check_step_ref(step[key], seen, swhere, key, report, need_list=False)
        fanout = step.get("fanout")
        if isinstance(fanout, dict):
            _check_step_ref(fanout["over"], seen, swhere, "fanout.over", report, need_list=True)
        output = step.get("output")
        if isinstance(output, dict) and not schema_exists(output["schema"]):
            report.error(swhere, f"output schema {output['schema']} not found")
        if step.get("workspace") == "git_clone" and not step.get("repo"):
            report.error(swhere, "workspace git_clone needs repo")
        for write in step.get("writes", []):
            connector = write.split(".", 1)[0]
            if connector not in connectors:
                report.error(swhere, f"write {write}: unknown connector {connector!r}")
                continue
            if connector not in playbook.get("connectors", []):
                report.error(swhere, f"write {write}: connector {connector!r} is not in the playbook connectors")
            known_write = operations.get(write)
            if known_write is None:
                report.error(swhere, f"write {write}: operation not declared by connector {connector!r}")
            elif known_write.cls == OperationClass.read:
                report.error(swhere, f"write {write}: is a read operation")
        if step.get("writes") and playbook.get("autonomy") == "read_only":
            report.error(swhere, "step has writes but the playbook is read_only")
        seen[sid] = step
    trigger = playbook.get("trigger")
    if isinstance(trigger, dict) and PLACEHOLDER.match(str(trigger.get("owner_user", ""))):
        report.warn(where, "trigger.owner_user is a placeholder; it is set to the user who installs the trigger")


def _check_step_ref(
    ref: str, seen: dict[str, dict[str, Any]], where: str, key: str, report: Report, *, need_list: bool
) -> None:
    match = STEP_REF.match(ref)
    if not match:
        report.error(where, f"{key} {ref!r} is not a step reference")
        return
    source_id, rest = match.group(1), match.group(2)
    source = seen.get(source_id)
    if source is None:
        report.error(where, f"{key} {ref!r}: step {source_id!r} is not an earlier step")
        return
    if rest == "outputs":
        if not source.get("fanout"):
            report.error(where, f"{key} {ref!r}: steps.{source_id}.outputs needs a fan-out step")
    elif need_list and not source.get("output"):
        report.error(where, f"{key} {ref!r}: fan-out source step {source_id!r} needs an output schema")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    packs_root = Path(args[0] if args else "packs").resolve()
    repo_root = packs_root.parent
    packs = discover(packs_root)
    report = lint(packs, repo_root)
    for warning in report.warnings:
        print(f"WARN  {warning}")
    for error in report.errors:
        print(f"ERROR {error}")
    print(f"packs: {len(packs)} checked, {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
