"""Agents, skills and playbooks (docs/03 §5 "Agents & skills", "Playbooks")."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from z_common.models import api_catalog as m
from z_common.models import api_missions
from z_common.models.manifests import AgentProfile

from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.api.paging import page
from z_server.catalog.validate import validate_agent, validate_playbook
from z_server.catalog.versions import bump_minor, newer
from z_server.db.models import Agent, AgentVersion, Playbook, PlaybookVersion, Skill, SkillVersion, Team
from z_server.errors import Problem
from z_server.ids import new_id

router = ContractRouter()
RENAME_FIELDS = {"display_name", "handle", "avatar"}
SERVER_ONLY = {"change_note", "owner_team"}


def model_aliases(ctx: Ctx) -> set[str]:
    aliases = set(ctx.state.config.get("inference", {}).get("aliases", []))
    for provider in ctx.state.config.get("providers", []):
        if provider.get("enabled"):
            aliases |= set(provider.get("aliases", []))
    return aliases


# ---------------------------------------------------------------- agents


async def _team_name(ctx: Ctx, team_id: str | None) -> str | None:
    team = await ctx.db.get(Team, team_id) if team_id else None
    return team.name if team else None


async def agent_out(ctx: Ctx, agent: Agent) -> m.Agent:
    return m.Agent(
        **{"visibility": agent.visibility, **agent.profile},
        id=agent.id,
        owner_team=await _team_name(ctx, agent.owner_team_id),
        status=agent.status,
        pack=agent.pack,
        current_missions=[],
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


async def find_agent(ctx: Ctx, ref: str) -> Agent:
    ref = ref.removeprefix("@")
    agent = (
        await ctx.db.get(Agent, ref)
        or (await ctx.db.execute(select(Agent).where(or_(Agent.name == ref, Agent.handle == ref)))).scalar_one_or_none()
    )
    if agent is None:
        raise Problem("not-found", f"no agent {ref!r}")
    return agent


async def _team_id(ctx: Ctx, team_name: str | None) -> str | None:
    if not team_name:
        return None
    team = (
        await ctx.db.execute(select(Team).where((Team.name == team_name) | (Team.id == team_name)))
    ).scalar_one_or_none()
    if team is None:
        raise Problem("validation", f"unknown team {team_name!r}")
    return team.id


async def _unique_name(ctx: Ctx, base: str) -> str:
    base = re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")[:56] or "agent"
    name, n = base, 2
    while (await ctx.db.execute(select(Agent.id).where(Agent.name == name))).first():
        name, n = f"{base}-{n}", n + 1
    return name


async def _create(ctx: Ctx, profile: dict[str, Any], owner_team_id: str | None, note: str) -> Agent:
    await validate_agent(ctx.db, profile, model_aliases(ctx))
    if owner_team_id:
        ctx.require("agents:write", owner_team_id)
    elif not ctx.user.has_org("agents:write"):
        raise Problem("forbidden", "team builders must set owner_team", permission="agents:write")
    agent = Agent(
        id=new_id("agt"),
        name=profile["name"],
        display_name=profile["display_name"],
        handle=profile["handle"],
        version=profile["version"],
        profile=profile,
        owner_team_id=owner_team_id,
        visibility=profile.get("visibility", "team"),
        status="available",
    )
    ctx.db.add(agent)
    await ctx.db.flush()
    ctx.db.add(
        AgentVersion(
            id=new_id("agv"),
            agent_id=agent.id,
            version=agent.version,
            profile=profile,
            change_note=note,
            created_by=ctx.user.username,
        )
    )
    return agent


@router.operation("listAgents")
async def list_agents(
    ctx: CtxDep, cursor: str | None = None, limit: int = 50, status: str | None = None, q: str | None = None
) -> dict[str, Any]:
    stmt = select(Agent)
    if status:
        stmt = stmt.where(Agent.status == status)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(Agent.name.ilike(like), Agent.display_name.ilike(like), Agent.handle.ilike(like)))
    rows, nxt = await page(ctx.db, stmt, Agent.id, cursor, limit)
    return {"items": [(await agent_out(ctx, a)).dump() for a in rows], "next_cursor": nxt}


@router.operation("createAgent", status_code=201)
async def create_agent(ctx: CtxDep, body: dict[str, Any]) -> m.Agent:
    if "template" in body and set(body) <= {"template", "display_name", "handle", "avatar", "owner_team"}:
        request = m.AgentFromTemplate.model_validate(body)
        source = await find_agent(ctx, request.template)
        profile = {
            **source.profile,
            "display_name": request.display_name,
            "handle": request.handle,
            "version": "1.0.0",
            "template": source.name,
        }
        profile["name"] = await _unique_name(ctx, request.handle)
        if request.avatar:
            profile["avatar"] = request.avatar.dump()
        owner = await _team_id(ctx, request.owner_team)
        note = f"from template {source.name}"
    else:
        owner = await _team_id(ctx, body.pop("owner_team", None))
        profile = AgentProfile.model_validate(body).dump()
        note = "created"
    agent = await _create(ctx, profile, owner, note)
    out = await agent_out(ctx, agent)
    await ctx.audit("agent.created", "agent", agent.id, team=await _team_name(ctx, owner), after=out.dump())
    return out


@router.operation("getAgent")
async def get_agent(ctx: CtxDep, id: str) -> m.Agent:
    return await agent_out(ctx, await find_agent(ctx, id))


@router.operation("patchAgent")
async def patch_agent(ctx: CtxDep, id: str, body: m.AgentPatch) -> m.Agent:
    agent = await find_agent(ctx, id)
    ctx.require("agents:write", agent.owner_team_id)
    changes = body.model_dump(mode="json", by_alias=True, exclude_unset=True)
    note = changes.pop("change_note", None)
    owner_change = "owner_team" in changes
    new_owner = await _team_id(ctx, changes.pop("owner_team", None)) if owner_change else agent.owner_team_id
    if owner_change and new_owner:
        ctx.require("agents:write", new_owner)
    if changes.get("name", agent.name) != agent.name:
        raise Problem("validation", "name is stable; rename changes display_name and handle")
    changes.pop("name", None)
    before = (await agent_out(ctx, agent)).dump()
    profile = {**agent.profile, **changes}
    rename_only = set(changes) <= RENAME_FIELDS
    if not rename_only:
        requested = changes.get("version")
        profile["version"] = requested if requested and newer(requested, agent.version) else bump_minor(agent.version)
    await validate_agent(ctx.db, profile, model_aliases(ctx), agent_id=agent.id)
    agent.profile, agent.display_name, agent.handle, agent.version = (
        profile,
        profile["display_name"],
        profile["handle"],
        profile["version"],
    )
    agent.visibility, agent.owner_team_id = profile.get("visibility", agent.visibility), new_owner
    if not rename_only:
        ctx.db.add(
            AgentVersion(
                id=new_id("agv"),
                agent_id=agent.id,
                version=agent.version,
                profile=profile,
                change_note=note,
                created_by=ctx.user.username,
            )
        )
    await ctx.db.flush()
    out = await agent_out(ctx, agent)
    await ctx.audit(
        "agent.renamed" if rename_only else "agent.updated",
        "agent",
        agent.id,
        team=out.owner_team,
        before=before,
        after=out.dump(),
    )
    return out


@router.operation("duplicateAgent", status_code=201)
async def duplicate_agent(ctx: CtxDep, id: str, body: m.AgentDuplicate) -> m.Agent:
    source = await find_agent(ctx, id)
    profile = {
        **source.profile,
        "display_name": body.display_name,
        "handle": body.handle,
        "version": "1.0.0",
        "template": source.name,
    }
    profile["name"] = await _unique_name(ctx, body.handle)
    if body.avatar:
        profile["avatar"] = body.avatar.dump()
    agent = await _create(
        ctx,
        profile,
        source.owner_team_id if not ctx.user.has_org("agents:write") else None,
        f"duplicated from {source.name}",
    )
    out = await agent_out(ctx, agent)
    await ctx.audit("agent.duplicated", "agent", agent.id, after={**out.dump(), "from": source.id})
    return out


TRIAL_PREFIX = "Trial: "
TRIAL_BUDGET = {"max_tokens": 200_000, "timeout_s": 900}


class AgentTest(BaseModel):
    """contracts AgentTest."""

    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=4000)
    inputs: dict[str, Any] | None = None


@router.operation("testAgent", status_code=201)
async def trial_agent(ctx: CtxDep, id: str, body: AgentTest) -> api_missions.Mission:
    """A read-only trial mission: one step with this agent (also a disabled one), no writes, no connectors beyond
    the agent's own read tools, the platform's default data class, a small budget and timeout."""
    from z_common.models.enums import Autonomy
    from z_common.models.manifests import MissionPlan, PlanStep

    from z_server.api.routes_missions import mission_out  # routes_missions imports this module
    from z_server.missions import service

    agent = await find_agent(ctx, id)
    ctx.require("agents:write", agent.owner_team_id)
    title = f"{TRIAL_PREFIX}{agent.display_name}"[:200]
    request = service.MissionRequest(
        title=title,
        prompt=body.prompt,
        playbook=None,
        inputs=body.inputs or {},
        plan=MissionPlan(steps=[PlanStep(id="trial", agent=agent.name, prompt=body.prompt)]),
        connectors=[],
        data_class=None,  # the platform default
        autonomy=Autonomy.read_only,
        plan_first=False,
        budget=dict(TRIAL_BUDGET),
        session_id=None,
        trial_agent=agent.name,
    )
    mission = await service.create(ctx.db, ctx.state, ctx.user, request)
    team = await _team_name(ctx, agent.owner_team_id)
    await ctx.audit(
        "mission.created",
        "mission",
        mission.id,
        team=mission.team_id,
        after={
            "title": mission.title,
            "trial_of": agent.name,
            "data_class": mission.data_class,
            "autonomy": "read_only",
        },
    )
    await service.start(ctx.db, ctx.state, mission)
    await ctx.audit("mission.started", "mission", mission.id, team=mission.team_id)
    await ctx.audit(
        "agent.tested",
        "agent",
        agent.id,
        team=team,
        after={"mission": mission.id, "agent_status": agent.status, "prompt_length": len(body.prompt)},
    )
    return await mission_out(ctx, mission)


@router.operation("listAgentVersions")
async def list_agent_versions(ctx: CtxDep, id: str) -> list[m.AgentVersion]:
    agent = await find_agent(ctx, id)
    rows = (
        await ctx.db.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id).order_by(AgentVersion.created_at.desc())
        )
    ).scalars()
    return [
        m.AgentVersion(
            version=v.version,
            created_at=v.created_at,
            created_by=v.created_by,
            change_note=v.change_note,
            profile=AgentProfile.model_validate(v.profile),
        )
        for v in rows
    ]


# ---------------------------------------------------------------- skills


async def _skill(ctx: Ctx, ref: str) -> Skill:
    skill = (
        await ctx.db.get(Skill, ref)
        or (await ctx.db.execute(select(Skill).where(Skill.name == ref))).scalar_one_or_none()
    )
    if skill is None:
        raise Problem("not-found", f"no skill {ref!r}")
    return skill


async def skill_out(ctx: Ctx, skill: Skill, *, content: bool = False) -> m.Skill:
    users = [
        a.name for a in (await ctx.db.execute(select(Agent))).scalars() if skill.name in a.profile.get("skills", [])
    ]
    latest = (
        await ctx.db.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill.id)
            .order_by(SkillVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    change = None
    if latest is not None:
        kind = (
            "rolled_back" if latest.version != skill.version else ("created" if latest.origin == "pack" else "edited")
        )
        change = m.SkillChange(kind=kind, at=skill.updated_at, by=latest.created_by, proposal_id=latest.proposal_id)
    return m.Skill(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        version=skill.version,
        origin=skill.origin,
        pack=skill.pack,
        path=skill.path,
        checksum=skill.checksum,
        evals=skill.evals,
        owner=skill.owner,
        agents=sorted(users),
        content=skill.content if content else None,
        last_change=change,
    )


@router.operation("listSkills")
async def list_skills(
    ctx: CtxDep, cursor: str | None = None, limit: int = 50, agent: str | None = None, origin: str | None = None
) -> dict[str, Any]:
    stmt = select(Skill)
    if origin:
        stmt = stmt.where(Skill.origin == origin)
    if agent:
        names = (await find_agent(ctx, agent)).profile.get("skills", [])
        stmt = stmt.where(Skill.name.in_(names))
    rows, nxt = await page(ctx.db, stmt, Skill.id, cursor, limit)
    return {"items": [(await skill_out(ctx, s)).dump() for s in rows], "next_cursor": nxt}


@router.operation("getSkill")
async def get_skill(ctx: CtxDep, id: str) -> m.Skill:
    return await skill_out(ctx, await _skill(ctx, id), content=True)


@router.operation("listSkillVersions")
async def list_skill_versions(ctx: CtxDep, id: str) -> list[m.SkillVersion]:
    skill = await _skill(ctx, id)
    rows = (
        await ctx.db.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id).order_by(SkillVersion.created_at.desc())
        )
    ).scalars()
    return [
        m.SkillVersion(
            version=v.version,
            checksum=v.checksum,
            created_at=v.created_at,
            created_by=v.created_by,
            origin=v.origin,
            proposal_id=v.proposal_id,
        )
        for v in rows
    ]


@router.operation("getSkillVersion")
async def get_skill_version(ctx: CtxDep, id: str, version: str) -> dict[str, Any]:
    """One version of a skill with its SKILL.md (SkillVersionDetail), e.g. to diff two versions."""
    skill = await _skill(ctx, id)
    row = (
        await ctx.db.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == version)
        )
    ).scalar_one_or_none()
    if row is None:
        raise Problem("not-found", f"skill {skill.name} has no version {version}")
    summary = m.SkillVersion(
        version=row.version,
        checksum=row.checksum,
        created_at=row.created_at,
        created_by=row.created_by,
        origin=row.origin,
        proposal_id=row.proposal_id,
    )
    return {**summary.dump(), "content": row.content}


@router.operation("rollbackSkill")
async def rollback_skill(ctx: CtxDep, id: str, body: m.SkillRollback) -> m.Skill:
    skill = await _skill(ctx, id)
    target = (
        await ctx.db.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == body.to_version)
        )
    ).scalar_one_or_none()
    if target is None:
        raise Problem("not-found", f"skill {skill.name} has no version {body.to_version}")
    if target.version == skill.version:
        raise Problem("conflict", "this is already the current version")
    before = {"version": skill.version, "checksum": skill.checksum}
    skill.content, skill.checksum, skill.version = target.content, target.checksum, target.version
    await ctx.db.flush()
    await ctx.audit(
        "skill.rolled_back",
        "skill",
        skill.id,
        before=before,
        after={"version": skill.version, "checksum": skill.checksum, "reason": body.reason},
    )
    return await skill_out(ctx, skill, content=True)


# ---------------------------------------------------------------- playbooks


async def _playbook(ctx: Ctx, ref: str) -> Playbook:
    row = (
        await ctx.db.get(Playbook, ref)
        or (await ctx.db.execute(select(Playbook).where(Playbook.name == ref))).scalar_one_or_none()
    )
    if row is None:
        raise Problem("not-found", f"no playbook {ref!r}")
    return row


async def playbook_out(ctx: Ctx, row: Playbook) -> m.Playbook:
    return m.Playbook(
        **row.manifest,
        id=row.id,
        pack=row.pack,
        owner_team=await _team_name(ctx, row.owner_team_id),
        usage_count=row.usage_count,
        last_run_at=row.last_run_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.operation("listPlaybooks")
async def list_playbooks(
    ctx: CtxDep, cursor: str | None = None, limit: int = 50, domain: str | None = None, q: str | None = None
) -> dict[str, Any]:
    stmt = select(Playbook)
    if q:
        stmt = stmt.where(Playbook.name.ilike(f"%{q.lower()}%"))
    rows, nxt = await page(ctx.db, stmt, Playbook.id, cursor, limit)
    items = [(await playbook_out(ctx, p)).dump() for p in rows]
    if domain:
        items = [i for i in items if i.get("domain") == domain]
    return {"items": items, "next_cursor": nxt}


@router.operation("createPlaybook", status_code=201)
async def create_playbook(ctx: CtxDep, body: dict[str, Any]) -> m.Playbook:
    return await insert_playbook(ctx, body, None)


async def insert_playbook(
    ctx: Ctx, body: dict[str, Any], team: str | None, origin: dict[str, Any] | None = None
) -> m.Playbook:
    if (await ctx.db.execute(select(Playbook.id).where(Playbook.name == body.get("name")))).first():
        raise Problem("conflict", "a playbook with this name exists")
    await validate_playbook(ctx.db, body, ctx.state.settings.packs_path, None)
    row = Playbook(id=new_id("pbk"), name=body["name"], version=body["version"], manifest=body, owner_team_id=team)
    ctx.db.add(row)
    await ctx.db.flush()
    ctx.db.add(
        PlaybookVersion(
            id=new_id("pbv"), playbook_id=row.id, version=row.version, manifest=body, created_by=ctx.user.username
        )
    )
    out = await playbook_out(ctx, row)
    await ctx.audit("playbook.created", "playbook", row.id, team=team, after={**body, **(origin or {})})
    return out


@router.operation("getPlaybook")
async def get_playbook(ctx: CtxDep, id: str) -> m.Playbook:
    return await playbook_out(ctx, await _playbook(ctx, id))


@router.operation("patchPlaybook")
async def patch_playbook(ctx: CtxDep, id: str, body: dict[str, Any]) -> m.Playbook:
    row = await _playbook(ctx, id)
    ctx.require("playbooks:write", row.owner_team_id)
    if body.get("name") != row.name:
        raise Problem("validation", "the playbook name cannot change")
    if not newer(str(body.get("version", "0.0.0")), row.version):
        body = {**body, "version": bump_minor(row.version)}
    await validate_playbook(ctx.db, body, ctx.state.settings.packs_path, row.pack)
    before = row.manifest
    row.manifest, row.version = body, body["version"]
    ctx.db.add(
        PlaybookVersion(
            id=new_id("pbv"), playbook_id=row.id, version=row.version, manifest=body, created_by=ctx.user.username
        )
    )
    await ctx.db.flush()
    await ctx.audit("playbook.updated", "playbook", row.id, before=before, after=body)
    return await playbook_out(ctx, row)


@router.operation("draftAgent")
async def draft_agent(ctx: CtxDep, body: dict[str, Any]) -> dict[str, Any]:
    """Agent builder: a drafted, sanitized profile to edit before creating it (nothing is created here)."""
    from z_server import agent_builder

    return await agent_builder.draft(ctx.db, ctx.state, ctx.user, str(body["description"]), body.get("team"))
