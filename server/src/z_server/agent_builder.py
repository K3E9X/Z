"""Agent builder (docs/09 "Agent builder", docs/03 §5 draftAgent): a plain-language description becomes a
drafted profile that a person edits and creates.

The planner model sees the existing skills, the read operations agents may use and the model aliases, and
answers with a profile, at most two clarifying questions, optional objectives and skills that do not exist
yet. The draft is then made safe whatever the model said: write operations are dropped (agents only propose
writes, invariant 4), unknown tools and skills are removed (unknown skills become `new_skills`), autonomy is
at most `propose`, limits are capped, and the name and handle are made unique. Nothing is created here.
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.manifests import AgentProfile, ObjectiveManifest

from z_server.db.models import Agent, Connector, Operation, Skill
from z_server.errors import Problem
from z_server.llm import chat
from z_server.rbac import Principal
from z_server.state import AppState

LOCAL = ("read", "edit", "terminal", "code", "documents", "browser")
MAX_LIMITS = {"max_steps": 120, "max_tokens": 2_000_000, "timeout_s": 3600}
DEFAULT_LIMITS = {"max_steps": 60, "max_tokens": 400_000, "timeout_s": 1800}
SYSTEM = (
    "You design AI agents for a security and IT operations team. From the user's description, draft one agent. "
    "Use existing skills when they fit; list skills that should exist but do not as new_skills. Grant the "
    "fewest tools: local tools (read, edit, terminal, code, documents, browser) and gateway read operations "
    "written gateway:<operation id>. Never grant write operations: agents propose writes for human approval. "
    "Ask at most two questions, only when the description leaves something important open. Answer with JSON "
    'only: {"profile": {"name": slug, "display_name": str, "handle": str, "role": str, "instructions": str, '
    '"model": alias, "skills": [names], "tools": [refs], "autonomy": "propose" | "read_only", '
    '"limits": {"max_steps": int, "max_tokens": int, "timeout_s": int}}, "questions": [str], '
    '"objectives": [{"title": str, "metric": {...}, "cadence": "cron"}], "new_skills": [{"name": slug, "purpose": str}]}'
)


def _slug(value: str, limit: int = 40) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "-", value.lower())).strip("-")[:limit] or "agent"


async def _unique(db: AsyncSession, name: str) -> str:
    taken = {n for (n,) in (await db.execute(select(Agent.name))).all()} | {
        h for (h,) in (await db.execute(select(Agent.handle))).all()
    }
    candidate, n = name, 2
    while candidate in taken:
        candidate = f"{name[:36]}-{n}"
        n += 1
    return candidate


async def _catalog(db: AsyncSession, state: AppState) -> tuple[dict[str, str], set[str], list[str]]:
    skills = {s.name: s.description for s in (await db.execute(select(Skill))).scalars()}
    reads = {
        op.id
        for op, c in (
            await db.execute(
                select(Operation, Connector)
                .join(Connector, Connector.id == Operation.connector_id)
                .where(Operation.op_class == "read", Operation.exposed.is_(True), Connector.enabled.is_(True))
            )
        ).all()
        if c.review_status == "approved"
    }
    aliases = [str(a) for a in (state.config.get("inference") or {}).get("aliases", []) if str(a) != "z-embed"]
    return skills, reads, aliases


def sanitize(
    raw: dict[str, Any], skills: dict[str, str], reads: set[str], aliases: list[str]
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    profile = dict(raw.get("profile") or {})
    new_skills = [
        {"name": _slug(str(s.get("name", ""))), "purpose": str(s.get("purpose", ""))[:300]}
        for s in raw.get("new_skills") or []
        if isinstance(s, dict) and s.get("name")
    ]
    wanted = [str(s) for s in profile.get("skills") or []]
    profile["skills"] = [s for s in wanted if s in skills]
    new_skills += [{"name": _slug(s), "purpose": "Requested by the draft"} for s in wanted if s not in skills]
    tools = []
    for ref in profile.get("tools") or []:
        ref = str(ref)
        if ref in LOCAL or (ref.startswith("gateway:") and ref.removeprefix("gateway:") in reads):
            tools.append(ref)
    profile["tools"] = sorted(set(tools), key=tools.index)
    profile["autonomy"] = "read_only" if profile.get("autonomy") == "read_only" else "propose"
    model = str(profile.get("model") or "")
    profile["model"] = (
        model if model in aliases else ("z-heavy" if "z-heavy" in aliases else (aliases or ["z-fast"])[0])
    )
    limits = dict(DEFAULT_LIMITS)
    for key, cap in MAX_LIMITS.items():
        with contextlib.suppress(TypeError, ValueError):
            limits[key] = max(1, min(int((profile.get("limits") or {}).get(key, limits[key])), cap))
    profile["limits"] = limits
    profile["version"] = "1.0.0"
    name = _slug(str(profile.get("name") or profile.get("display_name") or "agent"), 64)
    profile["name"] = name
    profile["handle"] = _slug(str(profile.get("handle") or name))
    profile["display_name"] = str(profile.get("display_name") or name.replace("-", " ").title())[:64]
    profile["memory"] = {"scopes": ["org", "team", "agent"], "top_k": 6}
    for key in ("template", "evals", "escalate_model"):
        profile.pop(key, None)
    return profile, new_skills


async def draft(
    db: AsyncSession, state: AppState, user: Principal, description: str, team: str | None
) -> dict[str, Any]:
    skills, reads, aliases = await _catalog(db, state)
    catalog = {
        "skills": [{"name": n, "description": d[:200]} for n, d in sorted(skills.items())],
        "gateway_read_operations": sorted(reads),
        "model_aliases": aliases,
        "team": team,
    }
    model = str((state.config.get("inference") or {}).get("planner_model", "z-heavy"))
    messages = [
        {"role": "system", "content": SYSTEM + "\n\nCatalog:\n" + json.dumps(catalog, ensure_ascii=False)},
        {"role": "user", "content": description},
    ]
    try:
        key = await chat.platform_key(db, state)
    except chat.ChatError as exc:
        raise Problem("unavailable", f"the agent builder is unavailable: {exc}") from exc
    problems: list[str] = []
    for _attempt in range(2):
        try:
            reply = await chat.complete(state, key, model, messages, max_tokens=3000, json_mode=True)
            raw = chat.parse_json(reply.text)
        except chat.ChatError as exc:
            raise Problem("unavailable", f"the agent builder is unavailable: {exc}") from exc
        profile, new_skills = sanitize(raw, skills, reads, aliases)
        profile["name"] = await _unique(db, profile["name"])
        profile["handle"] = await _unique(db, profile["handle"])
        try:
            AgentProfile.model_validate(profile)
        except ValidationError as exc:
            problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5]]
            messages += [
                {"role": "assistant", "content": reply.text},
                {
                    "role": "user",
                    "content": "Fix these problems and answer with the whole JSON again: " + "; ".join(problems),
                },
            ]
            continue
        objectives = []
        for item in raw.get("objectives") or []:
            if not isinstance(item, dict):
                continue
            try:
                objectives.append(ObjectiveManifest.model_validate({**item, "agent": profile["name"]}).dump())
            except ValidationError:
                continue
        questions = [str(q)[:300] for q in raw.get("questions") or [] if q][:2]
        out: dict[str, Any] = {
            "profile": profile,
            "questions": questions,
            "objectives": objectives,
            "new_skills": new_skills,
        }
        return out
    raise Problem("unavailable", "the agent builder returned an invalid profile twice: " + "; ".join(problems[:3]))
