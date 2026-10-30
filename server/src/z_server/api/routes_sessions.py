"""Sessions (docs/03 §5, docs/10 §1): conversations that start missions, stored server-side for `z -c` and the web.

A message is a request: Z drafts a plan (one step for a single `@agent`, else the planner model), creates the
mission in the session and starts it, or leaves it planned when the user asked for plan first. Problems the
user can act on (an unknown @agent, the planner unavailable, a budget reached) become the assistant's reply
instead of an error, so the conversation keeps its history.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from z_common.models import api_missions as mm
from z_common.models import api_ops as m

from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.api.paging import page
from z_server.api.routes_missions import _create, mission_out
from z_server.db.base import utcnow
from z_server.db.models import ChatMessage, ChatSession, Mission
from z_server.errors import Problem
from z_server.ids import new_id
from z_server.missions import planner

router = ContractRouter()
TITLE_CHARS = 80
USER_FACING = {"validation", "unavailable", "budget-exceeded", "conflict", "forbidden"}


async def _own(ctx: Ctx, session_id: str) -> ChatSession:
    row = await ctx.db.get(ChatSession, session_id)
    if row is None or row.user_id != ctx.user.user_id:
        raise Problem("not-found", "no such session")
    return row


async def _missions(ctx: Ctx, session_ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {sid: [] for sid in session_ids}
    if session_ids:
        rows = await ctx.db.execute(
            select(Mission.session_id, Mission.id).where(Mission.session_id.in_(session_ids)).order_by(Mission.id)
        )
        for sid, mid in rows.all():
            out[str(sid)].append(mid)
    return out


def session_out(row: ChatSession, username: str, missions: list[str]) -> dict[str, Any]:
    return {
        "id": row.id,
        "user": username,
        "surface": row.surface,
        "title": row.title or "New session",
        "missions": missions,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def message_out(row: ChatMessage) -> m.SessionMessage:
    return m.SessionMessage.model_validate(
        {"role": row.role, "text": row.text, "ts": row.ts, "mission_id": row.mission_id}
    )


@router.operation("listSessions")
async def list_sessions(
    ctx: CtxDep, cursor: str | None = None, limit: int = 50, surface: str | None = None
) -> dict[str, Any]:
    stmt = select(ChatSession).where(ChatSession.user_id == ctx.user.user_id)
    if surface:
        stmt = stmt.where(ChatSession.surface == surface)
    rows, nxt = await page(ctx.db, stmt, ChatSession.id, cursor, limit)
    missions = await _missions(ctx, [r.id for r in rows])
    return {"items": [session_out(r, ctx.user.username, missions[r.id]) for r in rows], "next_cursor": nxt}


@router.operation("createSession", status_code=201)
async def create_session(ctx: CtxDep, body: m.SessionCreate) -> m.Session:
    row = ChatSession(
        id=new_id("ses"), user_id=ctx.user.user_id, surface=body.surface.value, title=(body.title or "")[:200]
    )
    ctx.db.add(row)
    await ctx.db.flush()
    await ctx.db.refresh(row)
    await ctx.audit("session.created", "session", row.id, after={"surface": row.surface})
    return m.Session.model_validate(session_out(row, ctx.user.username, []))


@router.operation("getSession")
async def get_session(ctx: CtxDep, id: str) -> m.SessionDetail:
    row = await _own(ctx, id)
    messages = (
        await ctx.db.execute(
            select(ChatMessage).where(ChatMessage.session_id == id).order_by(ChatMessage.ts, ChatMessage.id)
        )
    ).scalars()
    missions = await _missions(ctx, [id])
    return m.SessionDetail.model_validate(
        {**session_out(row, ctx.user.username, missions[id]), "messages": [message_out(r).dump() for r in messages]}
    )


def _describe(mission: Mission, plan: Any, suggested: str | None) -> str:
    steps = " → ".join(f"{s.title or s.id} (@{s.agent})" for s in plan.steps)
    head = (
        f"Drafted a plan for “{mission.title}”. Nothing runs until you accept it."
        if mission.plan_first
        else f"Started “{mission.title}”."
    )
    lines = [head, f"Steps: {steps}."]
    if plan.writes:
        lines.append(f"Changes need your approval before they happen: {', '.join(plan.writes)}.")
    if suggested:
        lines.append(f"The playbook {suggested} covers this kind of request; run it next time for a reviewed plan.")
    return "\n".join(lines)


@router.operation("postSessionMessage")
async def post_session_message(ctx: CtxDep, id: str, body: m.SessionMessageIn) -> m.SessionReply:
    session = await _own(ctx, id)
    text = body.text.strip()
    if not text:
        raise Problem("validation", "the message is empty")
    if ctx.user.teams_with("missions:start") == set():
        raise Problem("forbidden", "starting missions needs the missions:start permission")
    said = ChatMessage(id=new_id("msg"), session_id=id, role="user", text=text)
    ctx.db.add(said)
    if not session.title:
        session.title = text[:TITLE_CHARS]
    await ctx.db.flush()
    mission: Mission | None = None
    try:
        draft = await planner.draft(ctx.db, ctx.state, ctx.user, text)  # outside the savepoint: keeps the platform key
        plan = draft.plan
        if body.model:
            for step in plan.steps:
                step.model = body.model
        create = mm.MissionCreate(
            prompt=text,
            title=text[:120],
            plan=plan,
            connectors=draft.parsed["connectors"] or None,
            autonomy=body.autonomy,
            plan_first=body.plan_first,
            session_id=id,
        )
        async with ctx.db.begin_nested():  # a refused start (budget) leaves no half-created mission
            mission = await _create(ctx, create)
        reply = _describe(mission, plan, draft.suggested_playbook)
    except Problem as exc:
        if exc.kind not in USER_FACING:
            raise
        mission = None
        reply = f"I could not start this: {exc.detail or exc.kind}"
    answer = ChatMessage(
        id=new_id("msg"), session_id=id, role="assistant", text=reply, mission_id=mission.id if mission else None
    )
    ctx.db.add(answer)
    session.updated_at = utcnow()
    await ctx.db.flush()
    await ctx.audit(
        "session.message", "session", id, after={"mission": mission.id if mission else None, "chars": len(text)}
    )
    return m.SessionReply.model_validate(
        {
            "messages": [message_out(said).dump(), message_out(answer).dump()],
            "mission": (await mission_out(ctx, mission)).dump() if mission else None,
        }
    )
