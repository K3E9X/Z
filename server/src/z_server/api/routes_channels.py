"""Channels admin and inbound endpoints (docs/05 §10). Inbound requests are authenticated by each platform's
signature, never by a Z session."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from z_server import channels
from z_server.api.framework import ContractRouter, Ctx, CtxDep
from z_server.db.models import Agent, Channel
from z_server.errors import Problem
from z_server.gateway.secrets import SecretResolver
from z_server.ids import new_id

router = ContractRouter()
MAX_BODY = 1024 * 1024


@router.operation("listChannels")
async def list_channels(ctx: CtxDep) -> list[dict[str, Any]]:
    rows = (await ctx.db.execute(select(Channel).order_by(Channel.created_at))).scalars()
    return [channels.channel_out(r) for r in rows]


@router.operation("createChannel", status_code=201)
async def create_channel(ctx: CtxDep, body: dict[str, Any]) -> dict[str, Any]:
    kind = str(body["kind"])
    config = dict(body.get("config") or {})
    channels.validate_config(kind, config)
    agent = str(body["default_agent"])
    if (await ctx.db.execute(select(Agent.id).where(Agent.name == agent))).first() is None:
        raise Problem("validation", f"no agent {agent}")
    if kind == "teams" and not config.get("app_id"):
        raise Problem("validation", "a Teams channel needs config.app_id")
    row = Channel(
        id=new_id("chn"),
        kind=kind,
        name=str(body.get("name") or kind)[:120],
        config=config,
        default_agent=agent,
        allowed_teams=list(body.get("allowed_teams") or []),
        identity_mapping=str(config.pop("identity_mapping", "sso_email")),
        enabled=bool(body.get("enabled", False)),
    )
    ctx.db.add(row)
    await ctx.db.flush()
    await ctx.db.refresh(row)
    await ctx.audit("channel.created", "channel", row.id, after={**channels.channel_out(row), "created_at": None})
    return channels.channel_out(row)


async def _channel(ctx: Ctx, kind: str) -> Channel:
    row = (
        (
            await ctx.db.execute(
                select(Channel).where(Channel.kind == kind, Channel.enabled.is_(True)).order_by(Channel.created_at)
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise Problem("not-found", f"no enabled {kind} channel")
    return row


def _resolver(ctx: Ctx) -> SecretResolver:
    return ctx.state.gateway.resolver if ctx.state.gateway else SecretResolver(ctx.state.http)


async def _read(request: Request) -> tuple[bytes, dict[str, str], dict[str, Any]]:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise Problem("validation", "body larger than 1 MB", status=413)
    headers = {k.lower(): v for k, v in request.headers.items()}
    if headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        return raw, headers, dict(parse_qsl(raw.decode("utf-8", errors="replace")))
    try:
        data = json.loads(raw or b"{}")
    except ValueError as exc:
        raise Problem("validation", "the body is not JSON") from exc
    if not isinstance(data, dict):
        raise Problem("validation", "the body must be a JSON object")
    return raw, headers, data


async def _act(ctx: Ctx, channel: Channel, inbound: channels.Inbound) -> channels.Outcome:
    outcome = await channels.handle(ctx.db, ctx.state, channel, inbound)
    ctx.audited = True  # channels.handle audits the decision (denied, task or mission)
    return outcome


@router.operation("channelSlackInbound", status_code=202)
async def slack_inbound(ctx: CtxDep, request: Request) -> JSONResponse:
    channel = await _channel(ctx, "slack")
    raw, headers, fields = await _read(request)
    await channels.verify_slack(_resolver(ctx), channel, headers, raw)
    if fields.get("type") == "url_verification":
        await ctx.audit("channel.verified", "channel", channel.id)
        return JSONResponse({"challenge": str(fields.get("challenge", ""))}, status_code=200)
    if fields.get("type") == "event_callback" and (fields.get("event") or {}).get("bot_id"):
        await ctx.audit("channel.ignored", "channel", channel.id, after={"reason": "bot message"})
        return JSONResponse({"task_id": None, "mission_id": None}, status_code=202)
    inbound = channels.parse_slack(fields)
    outcome = await _act(ctx, channel, inbound)
    if "command" in fields:  # the slash command answer is shown to the user directly
        return JSONResponse({"response_type": "ephemeral", "text": outcome.reply}, status_code=200)
    ctx.after_commit.append(lambda: channels.reply(ctx.state, channel, inbound, outcome.reply))
    return JSONResponse({"task_id": outcome.task_id, "mission_id": outcome.mission_id}, status_code=202)


@router.operation("channelMattermostInbound", status_code=202)
async def mattermost_inbound(ctx: CtxDep, request: Request) -> JSONResponse:
    channel = await _channel(ctx, "mattermost")
    _raw, _headers, fields = await _read(request)
    await channels.verify_mattermost(_resolver(ctx), channel, fields)
    outcome = await _act(ctx, channel, channels.parse_mattermost(fields))
    return JSONResponse({"response_type": "ephemeral", "text": outcome.reply}, status_code=200)


@router.operation("channelTeamsInbound", status_code=202)
async def teams_inbound(ctx: CtxDep, request: Request) -> JSONResponse:
    channel = await _channel(ctx, "teams")
    _raw, headers, activity = await _read(request)
    await channels.verify_teams(ctx.state, channel, headers, activity)
    if activity.get("type") != "message":
        await ctx.audit("channel.ignored", "channel", channel.id, after={"reason": f"activity {activity.get('type')}"})
        return JSONResponse({"task_id": None, "mission_id": None}, status_code=202)
    inbound = await channels.parse_teams(ctx.state, _resolver(ctx), channel, activity)
    outcome = await _act(ctx, channel, inbound)
    ctx.after_commit.append(lambda: channels.reply(ctx.state, channel, inbound, outcome.reply))
    return JSONResponse({"task_id": outcome.task_id, "mission_id": outcome.mission_id}, status_code=202)


@router.operation("channelEmailInbound", status_code=202)
async def email_inbound(ctx: CtxDep, request: Request) -> JSONResponse:
    channel = await _channel(ctx, "email")
    raw, headers, body = await _read(request)
    await channels.verify_email(_resolver(ctx), channel, headers, raw)
    outcome = await _act(ctx, channel, channels.parse_email(body))
    return JSONResponse({"task_id": outcome.task_id, "mission_id": outcome.mission_id}, status_code=202)
