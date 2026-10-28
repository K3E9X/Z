"""Validation of agents and playbooks created or edited through the API, with the same rules as pack lint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.contracts import errors as schema_errors
from z_common.models.enums import OperationClass
from z_common.packs.classify import glob_match, is_glob
from z_common.packs.lint import KnownOperation, Report, lint_playbook

from z_server.catalog import schemas
from z_server.db.models import Agent, Connector, Operation, Skill
from z_server.errors import Problem


def _raise(problems: list[str]) -> None:
    if problems:
        raise Problem("validation", "; ".join(problems[:5]), errors=[{"path": "", "message": p} for p in problems])


async def known_operations(db: AsyncSession) -> dict[str, KnownOperation]:
    out = {}
    for op in (await db.execute(select(Operation))).scalars():
        connector, _, local = op.id.partition(".")
        out[op.id] = KnownOperation(connector, local, OperationClass(op.op_class))
    return out


async def validate_agent(
    db: AsyncSession, profile: dict[str, Any], aliases: set[str], *, agent_id: str | None = None
) -> None:
    problems = schema_errors("agent.schema.json", profile)
    _raise(problems)
    clash = (
        (await db.execute(select(Agent).where((Agent.handle == profile["handle"]) | (Agent.name == profile["name"]))))
        .scalars()
        .all()
    )
    for other in clash:
        if other.id != agent_id:
            problems.append(f"name or handle already used by @{other.handle}")
    skills = {s for (s,) in (await db.execute(select(Skill.name))).all()}
    problems += [f"skill {s!r} does not exist" for s in profile.get("skills", []) if s not in skills]
    connectors = {c for (c,) in (await db.execute(select(Connector.name))).all()}
    ops = await known_operations(db)
    for tool in profile.get("tools", []):
        if not tool.startswith("gateway:") or tool == "gateway:@read":
            continue
        pattern = tool.removeprefix("gateway:")
        if pattern.split(".", 1)[0] not in connectors:
            problems.append(f"tool {tool}: unknown connector")
        elif not is_glob(pattern) and pattern in ops and ops[pattern].cls != OperationClass.read:
            problems.append(f"tool {tool}: writes are proposed, never listed as tools")
        elif is_glob(pattern) and not any(glob_match(pattern, i) for i in ops):
            problems.append(f"tool {tool}: matches no operation")
    for key in ("model", "escalate_model"):
        if profile.get(key) and profile[key] not in aliases:
            problems.append(f"{key} {profile[key]!r} is not a configured model alias")
    _raise(problems)


async def validate_playbook(db: AsyncSession, manifest: dict[str, Any], packs_root: Path, pack: str | None) -> None:
    _raise(schema_errors("playbook.schema.json", manifest))
    agents = {a for (a,) in (await db.execute(select(Agent.name))).all()}
    connectors = {c for (c,) in (await db.execute(select(Connector.name))).all()}
    report = Report()
    lint_playbook(
        manifest,
        manifest["name"],
        agents,
        connectors,
        await known_operations(db),
        lambda p: schemas.resolve(packs_root, pack, p) is not None,
        report,
    )
    _raise(report.errors)
