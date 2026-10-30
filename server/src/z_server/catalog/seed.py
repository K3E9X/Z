"""Load packs into the catalog (`z-server seed --packs packs/`). Idempotent.

Rules, so a pack update never overwrites what a team changed:
- agents are created once; an existing agent (maybe renamed or edited) is kept;
- skills and playbooks from the same pack move to a newer pack version only when the current version
  came from the pack (no local edit); otherwise they are kept and reported;
- connectors are created once, `pending_review`, with every operation hidden until an admin approves.
Seeding refuses packs with lint errors.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.connectors.operations import materialize
from z_common.packs import Pack, discover
from z_common.packs.lint import lint

from z_server import audit
from z_server.catalog.versions import newer
from z_server.db.models import (
    Agent,
    AgentVersion,
    Connector,
    Operation,
    PackRecord,
    Playbook,
    PlaybookVersion,
    Skill,
    SkillVersion,
)
from z_server.ids import new_id


class SeedError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems[:10]))
        self.problems = problems


@dataclass
class SeedReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"created": self.created, "updated": self.updated, "kept": self.kept, "warnings": self.warnings}


def checksum_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def checksum_dir(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def pack_actor(pack: str) -> str:
    return f"pack:{pack}"


async def seed(db: AsyncSession, packs_root: Path, actor: audit.Actor, only: set[str] | None = None) -> SeedReport:
    packs = [p for p in discover(packs_root) if only is None or p.name in only]
    lint_report = lint(discover(packs_root), packs_root.parent)
    if lint_report.errors:
        raise SeedError(lint_report.errors)
    report = SeedReport(warnings=list(lint_report.warnings))
    for pack in packs:
        await _seed_skills(db, pack, report)
    for pack in packs:
        await _seed_connectors(db, pack, report)
    for pack in packs:
        await _seed_agents(db, pack, report)
        await _seed_playbooks(db, pack, report)
        record = await db.get(PackRecord, pack.name)
        version = str(pack.manifest.data["version"])
        if record is None:
            db.add(PackRecord(name=pack.name, version=version, path=str(pack.root), checksum=checksum_dir(pack.root)))
        else:
            record.version, record.path, record.checksum = version, str(pack.root), checksum_dir(pack.root)
    await db.flush()
    await audit.record(db, actor, "catalog.seeded", "packs", ",".join(p.name for p in packs), after=report.as_dict())
    return report


async def _seed_skills(db: AsyncSession, pack: Pack, report: SeedReport) -> None:
    for skill in pack.skills:
        content = skill.path.read_text(encoding="utf-8")
        version = str(skill.front_matter.get("version", "1.0.0"))
        digest = checksum_text(content)
        row = (await db.execute(select(Skill).where(Skill.name == skill.name))).scalar_one_or_none()
        label = f"skill {skill.name}@{version}"
        if row is None:
            row = Skill(
                id=new_id("skl"),
                name=skill.name,
                description=str(skill.front_matter["description"]),
                version=version,
                origin="pack",
                pack=pack.name,
                path=str(skill.path.relative_to(pack.root)),
                content=content,
                checksum=digest,
                evals=skill.front_matter.get("evals"),
                owner=skill.front_matter.get("owner"),
            )
            db.add(row)
            await db.flush()
            db.add(
                SkillVersion(
                    id=new_id("skv"),
                    skill_id=row.id,
                    version=version,
                    content=content,
                    checksum=digest,
                    origin="pack",
                    created_by=pack_actor(pack.name),
                )
            )
            report.created.append(label)
        elif row.checksum == digest:
            continue
        elif row.origin == "pack" and row.pack == pack.name and newer(version, row.version):
            row.content, row.checksum, row.version, row.description = (
                content,
                digest,
                version,
                str(skill.front_matter["description"]),
            )
            db.add(
                SkillVersion(
                    id=new_id("skv"),
                    skill_id=row.id,
                    version=version,
                    content=content,
                    checksum=digest,
                    origin="pack",
                    created_by=pack_actor(pack.name),
                )
            )
            report.updated.append(label)
        else:
            report.kept.append(
                f"skill {skill.name}: local version {row.version} kept ({'not newer' if row.origin == 'pack' else row.origin + ' changes'})"
            )


async def _seed_connectors(db: AsyncSession, pack: Pack, report: SeedReport) -> None:
    for doc in pack.connectors:
        manifest: dict[str, Any] = doc.data
        name = str(manifest["name"])
        if (await db.execute(select(Connector).where(Connector.name == name))).scalar_one_or_none() is not None:
            report.kept.append(f"connector {name}: existing configuration kept")
            continue
        connector = Connector(
            id=new_id("con"),
            name=name,
            kind=str(manifest["kind"]),
            display_name=str(manifest.get("display_name") or name),
            base_url=manifest.get("base_url"),
            data_class=str(manifest.get("data_class", "C2")),
            review_status="pending_review",
            enabled=True,
            manifest=manifest,
            pack=pack.name,
            health={"status": "disabled", "detail": "waiting for exposure review"},
        )
        db.add(connector)
        await db.flush()
        for op in materialize(manifest, doc.path.parent):
            db.add(
                Operation(
                    id=f"{name}.{op.local_id}",
                    connector_id=connector.id,
                    local_id=op.local_id,
                    source_ref=op.source_ref,
                    description=op.description,
                    op_class=op.op_class.value,
                    classifier_class=op.classifier_class.value,
                    exposed=False,
                    proposed_exposed=op.proposed_exposed,
                    input_schema=op.input_schema,
                )
            )
        from z_server.gateway.admin import sync_credentials

        await sync_credentials(db, connector)
        report.created.append(f"connector {name} (pending review)")


async def _seed_agents(db: AsyncSession, pack: Pack, report: SeedReport) -> None:
    for doc in pack.agents:
        profile: dict[str, Any] = doc.data
        name = str(profile["name"])
        if (await db.execute(select(Agent).where(Agent.name == name))).scalar_one_or_none() is not None:
            report.kept.append(f"agent {name}: existing profile kept")
            continue
        agent = Agent(
            id=new_id("agt"),
            name=name,
            display_name=str(profile["display_name"]),
            handle=str(profile["handle"]),
            version=str(profile["version"]),
            profile=profile,
            visibility=str(profile.get("visibility", "team")),
            status="available",
            pack=pack.name,
        )
        db.add(agent)
        await db.flush()
        db.add(
            AgentVersion(
                id=new_id("agv"),
                agent_id=agent.id,
                version=agent.version,
                profile=profile,
                change_note=f"from pack {pack.name}",
                created_by=pack_actor(pack.name),
            )
        )
        report.created.append(f"agent {name}@{agent.version}")


async def _seed_playbooks(db: AsyncSession, pack: Pack, report: SeedReport) -> None:
    for doc in pack.playbooks:
        manifest: dict[str, Any] = doc.data
        name, version = str(manifest["name"]), str(manifest["version"])
        row = (await db.execute(select(Playbook).where(Playbook.name == name))).scalar_one_or_none()
        if row is None:
            row = Playbook(id=new_id("pbk"), name=name, version=version, manifest=manifest, pack=pack.name)
            db.add(row)
            await db.flush()
            db.add(
                PlaybookVersion(
                    id=new_id("pbv"),
                    playbook_id=row.id,
                    version=version,
                    manifest=manifest,
                    created_by=pack_actor(pack.name),
                )
            )
            report.created.append(f"playbook {name}@{version}")
            continue
        current = (
            await db.execute(
                select(PlaybookVersion).where(
                    PlaybookVersion.playbook_id == row.id, PlaybookVersion.version == row.version
                )
            )
        ).scalar_one_or_none()
        from_pack = current is not None and current.created_by == pack_actor(pack.name)
        if row.pack == pack.name and newer(version, row.version) and from_pack:
            row.version, row.manifest = version, manifest
            db.add(
                PlaybookVersion(
                    id=new_id("pbv"),
                    playbook_id=row.id,
                    version=version,
                    manifest=manifest,
                    created_by=pack_actor(pack.name),
                )
            )
            report.updated.append(f"playbook {name}@{version}")
        elif row.version != version:
            report.kept.append(f"playbook {name}: local version {row.version} kept")
