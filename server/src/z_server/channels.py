"""Channels (docs/05 §10): using Z from Slack, Mattermost, Teams and email.

Inbound messages are authenticated per kind (Slack signing secret, Mattermost token, Bot Framework JWT, Z
HMAC for the mail relay), mapped to a Z user (unknown or disallowed people cannot start anything), scanned by
the judge (channel content is untrusted data), then:
- chat: a mention of `@agent` (or the channel's default agent) starts a mission as that person, with their
  rights; blocked content becomes a task for a human instead;
- email: a task with the mail and its attachments, never an automatic start.
Replies go back where the message came from (the slash command answer, the Slack/Teams thread). Approvals
stay in the Z app; destructive and two-approver actions are never approved from a chat (docs/05 §10).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from jwt import PyJWK
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import JudgeVerdict

from z_server import audit, judge
from z_server.auth import identity
from z_server.db.models import Agent, Channel, Task, Team, User
from z_server.errors import Problem
from z_server.gateway.secrets import SecretError, SecretResolver
from z_server.ids import new_id
from z_server.intake import service as intake
from z_server.llm import policy
from z_server.missions import planner, service
from z_server.state import AppState

log = logging.getLogger("z.channels")
SKEW_S = 300
MAX_TEXT = 8000
SECRET_KEYS = {"signing_secret", "bot_token", "token", "app_password", "secret"}
BOTFRAMEWORK = {
    "openid_metadata_url": "https://login.botframework.com/v1/.well-known/openidconfiguration",
    "issuer": "https://api.botframework.com",
    "token_url": "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token",
    "scope": "https://api.botframework.com/.default",
}
MENTION_TAGS = re.compile(r"<at>[^<]*</at>|<@[A-Z0-9]+(?:\|[^>]*)?>", re.IGNORECASE)


@dataclass
class Inbound:
    text: str
    username: str | None = None
    email: str | None = None
    external_ref: str | None = None
    reply_url: str | None = None  # Slack/Mattermost response_url
    teams: dict[str, Any] | None = None  # serviceUrl, conversation id, activity id
    slack: dict[str, Any] | None = None  # channel + thread for chat.postMessage
    attachments: list[dict[str, Any]] = field(default_factory=list)
    subject: str | None = None


@dataclass
class Outcome:
    task_id: str | None = None
    mission_id: str | None = None
    reply: str = ""


def validate_config(kind: str, config: dict[str, Any]) -> None:
    for key, value in config.items():
        if key in SECRET_KEYS and (
            not isinstance(value, str)
            or value.split(":", 1)[0] not in {"env", "file", "vault", "kubernetes"}
            or ":" not in value
        ):
            raise Problem(
                "validation",
                f"config.{key} must be a secret reference (env:, file:, vault:, kubernetes:), never a value",
            )
    needed = {
        "slack": ["signing_secret"],
        "mattermost": ["token"],
        "teams": ["app_id", "app_password"],
        "email": ["secret"],
    }[kind]
    missing = [k for k in needed if not config.get(k)]
    if missing:
        raise Problem("validation", f"a {kind} channel needs config.{', config.'.join(missing)}")


async def _secret(resolver: SecretResolver, channel: Channel, key: str) -> str:
    ref = (channel.config or {}).get(key)
    if not ref:
        raise Problem("unauthenticated", f"the {channel.kind} channel has no {key}")
    try:
        return await resolver.resolve(str(ref))
    except SecretError as exc:
        raise Problem("unavailable", f"the {channel.kind} channel secret is unavailable") from exc


# ---------------------------------------------------------------- authentication and parsing


async def verify_slack(resolver: SecretResolver, channel: Channel, headers: dict[str, str], body: bytes) -> None:
    stamp = headers.get("x-slack-request-timestamp", "")
    if not stamp.isdigit() or abs(time.time() - int(stamp)) > SKEW_S:
        raise Problem("unauthenticated", "X-Slack-Request-Timestamp missing or outside the 5 minute window")
    secret = await _secret(resolver, channel, "signing_secret")
    expected = "v0=" + hmac.new(secret.encode(), f"v0:{stamp}:".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(headers.get("x-slack-signature", ""), expected):
        raise Problem("unauthenticated", "bad X-Slack-Signature")


async def verify_mattermost(resolver: SecretResolver, channel: Channel, fields: dict[str, Any]) -> None:
    secret = await _secret(resolver, channel, "token")
    if not hmac.compare_digest(str(fields.get("token") or ""), secret):
        raise Problem("unauthenticated", "bad Mattermost token")


async def verify_email(resolver: SecretResolver, channel: Channel, headers: dict[str, str], body: bytes) -> None:
    stamp = headers.get("x-z-timestamp", "")
    if not stamp.isdigit() or abs(time.time() - int(stamp)) > SKEW_S:
        raise Problem("unauthenticated", "X-Z-Timestamp missing or outside the 5 minute window")
    secret = await _secret(resolver, channel, "secret")
    expected = "sha256=" + hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(headers.get("x-z-signature", ""), expected):
        raise Problem("unauthenticated", "bad X-Z-Signature")


async def _botframework_keys(state: AppState, metadata_url: str) -> dict[str, PyJWK]:
    cache = state.cache.get(f"bf_keys:{metadata_url}")
    if cache and cache[0] > time.monotonic():
        return dict(cache[1])
    try:
        meta = (await state.http.get(metadata_url, timeout=10)).json()
        keys = (await state.http.get(meta["jwks_uri"], timeout=10)).json().get("keys", [])
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise Problem("unavailable", "the Bot Framework signing keys are unreachable") from exc
    parsed = {k.get("kid", ""): PyJWK.from_dict(k) for k in keys if k.get("kty") == "RSA"}
    state.cache[f"bf_keys:{metadata_url}"] = (time.monotonic() + 86400, parsed)
    return parsed


async def verify_teams(state: AppState, channel: Channel, headers: dict[str, str], activity: dict[str, Any]) -> None:
    cfg = {**BOTFRAMEWORK, **(channel.config or {})}
    token = headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise Problem("unauthenticated", "missing Bot Connector token")
    try:
        kid = jwt.get_unverified_header(token).get("kid", "")
    except jwt.PyJWTError as exc:
        raise Problem("unauthenticated", "malformed Bot Connector token") from exc
    keys = await _botframework_keys(state, str(cfg["openid_metadata_url"]))
    key = keys.get(kid)
    if key is None:
        state.cache.pop(f"bf_keys:{cfg['openid_metadata_url']}", None)
        raise Problem("unauthenticated", "unknown Bot Connector signing key")
    try:
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], audience=str(cfg["app_id"]), issuer=str(cfg["issuer"]), leeway=300
        )
    except jwt.PyJWTError as exc:
        raise Problem("unauthenticated", f"Bot Connector token refused ({type(exc).__name__})") from exc
    if claims.get("serviceUrl") and claims["serviceUrl"] != activity.get("serviceUrl"):
        raise Problem("unauthenticated", "serviceUrl does not match the token")


def parse_slack(fields: dict[str, Any]) -> Inbound:
    if "command" in fields:  # slash command
        return Inbound(
            text=str(fields.get("text") or ""),
            username=fields.get("user_name"),
            external_ref=f"slack:{fields.get('trigger_id') or fields.get('user_id')}",
            reply_url=fields.get("response_url"),
        )
    event = fields.get("event") or {}
    return Inbound(
        text=str(event.get("text") or ""),
        username=event.get("user"),
        external_ref=f"slack:{fields.get('event_id')}",
        slack={"channel": event.get("channel"), "thread_ts": event.get("thread_ts") or event.get("ts")},
    )


def parse_mattermost(fields: dict[str, Any]) -> Inbound:
    text = str(fields.get("text") or "")
    trigger = fields.get("trigger_word")
    if trigger and text.startswith(str(trigger)):
        text = text[len(str(trigger)) :]
    return Inbound(
        text=text,
        username=fields.get("user_name"),
        external_ref=f"mattermost:{fields.get('post_id') or fields.get('trigger_id') or fields.get('user_id')}",
        reply_url=fields.get("response_url"),
    )


def parse_email(body: dict[str, Any]) -> Inbound:
    sender = str(body.get("from") or "")
    match = re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", sender)
    return Inbound(
        text=str(body.get("text") or "")[:MAX_TEXT],
        email=match.group(0).lower() if match else None,
        external_ref=f"email:{body.get('message_id') or hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]}",
        attachments=[a for a in body.get("attachments") or [] if isinstance(a, dict)][:10],
        subject=str(body.get("subject") or "(no subject)")[:300],
    )


async def bot_token(state: AppState, resolver: SecretResolver, channel: Channel) -> str:
    cfg = {**BOTFRAMEWORK, **(channel.config or {})}
    hit = state.cache.get(f"bf_token:{channel.id}")
    if hit and hit[0] > time.monotonic():
        return str(hit[1])
    password = await _secret(resolver, channel, "app_password")
    try:
        resp = await state.http.post(
            str(cfg["token_url"]),
            data={
                "grant_type": "client_credentials",
                "client_id": str(cfg["app_id"]),
                "client_secret": password,
                "scope": str(cfg["scope"]),
            },
            timeout=15,
        )
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise Problem("unavailable", "the Bot Framework token endpoint is unreachable") from exc
    if resp.status_code != 200 or "access_token" not in body:
        raise Problem("unavailable", "the Bot Framework refused the bot credentials")
    state.cache[f"bf_token:{channel.id}"] = (
        time.monotonic() + int(body.get("expires_in", 3600)) - 120,
        body["access_token"],
    )
    return str(body["access_token"])


async def parse_teams(state: AppState, resolver: SecretResolver, channel: Channel, activity: dict[str, Any]) -> Inbound:
    service_url = str(activity.get("serviceUrl") or "").rstrip("/")
    conversation = (activity.get("conversation") or {}).get("id")
    sender = (activity.get("from") or {}).get("id")
    email = None
    if service_url and conversation and sender:  # the activity has no e-mail: ask the Bot Connector who it is
        token = await bot_token(state, resolver, channel)
        try:
            member = await state.http.get(
                f"{service_url}/v3/conversations/{conversation}/members/{sender}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            data = member.json() if member.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            data = {}
        key = "userPrincipalName" if channel.identity_mapping == "sso_upn" else "email"
        email = str(data.get(key) or data.get("email") or "").lower() or None
    return Inbound(
        text=MENTION_TAGS.sub("", str(activity.get("text") or "")).strip(),
        email=email,
        external_ref=f"teams:{activity.get('id')}",
        teams={"service_url": service_url, "conversation": conversation, "activity": activity.get("id")},
    )


# ---------------------------------------------------------------- acting on a message


async def _user(db: AsyncSession, inbound: Inbound) -> User | None:
    if inbound.email:
        row = (await db.execute(select(User).where(func.lower(User.email) == inbound.email))).scalar_one_or_none()
        if row is not None:
            return row
    if inbound.username:
        return (await db.execute(select(User).where(User.username == inbound.username))).scalar_one_or_none()
    return None


async def handle(db: AsyncSession, state: AppState, channel: Channel, inbound: Inbound) -> Outcome:
    actor = audit.Actor(f"channel:{channel.id}")
    user = await _user(db, inbound)
    if user is None or user.status != "active":
        await audit.record(
            db, actor, "channel.denied", "channel", channel.id, result="denied", after={"reason": "unknown user"}
        )
        return Outcome(reply="Z does not know you: ask an administrator to give you access.")
    principal = await identity.build_principal(db, user, state.config_auth, kind="channel")
    if channel.allowed_teams:
        names = {t.name for t in (await db.execute(select(Team).where(Team.id.in_(sorted(principal.teams))))).scalars()}
        if not (set(principal.teams) | names) & set(channel.allowed_teams):
            await audit.record(
                db,
                actor,
                "channel.denied",
                "channel",
                channel.id,
                result="denied",
                runs_as=user.username,
                after={"reason": "team not allowed"},
            )
            return Outcome(reply="This channel is not open to your team.")
    text = inbound.text.strip()[:MAX_TEXT]
    verdict = judge.scan(
        "\n".join([inbound.subject or "", text, *[str(a.get("name", "")) for a in inbound.attachments]])
    )
    if channel.kind == "email" or verdict.verdict == JudgeVerdict.blocked or not text:
        return await _task(db, state, channel, inbound, user, verdict)
    if not principal.can("missions:start", (principal.teams and sorted(principal.teams)[0]) or None):
        await audit.record(
            db,
            actor,
            "channel.denied",
            "channel",
            channel.id,
            result="denied",
            runs_as=user.username,
            after={"reason": "missions:start"},
        )
        return Outcome(reply="You cannot start missions in Z.")
    agents = None if planner.MENTION.search(text) else [channel.default_agent]
    try:
        async with db.begin_nested():
            draft = await planner.draft(db, state, principal, text, agents)
            mission = await service.create(
                db,
                state,
                principal,
                service.MissionRequest(
                    title=text[:120],
                    prompt=text,
                    playbook=None,
                    inputs={},
                    plan=draft.plan,
                    connectors=draft.parsed["connectors"],
                    data_class=None,
                    autonomy=None,
                    plan_first=False,
                    budget={},
                    session_id=None,
                ),
            )
            await service.start(db, state, mission)
    except Problem as exc:
        return Outcome(reply=f"Z could not start this: {exc.detail or exc.kind}")
    await audit.record(
        db,
        actor,
        "mission.started",
        "mission",
        mission.id,
        team=mission.team_id,
        runs_as=user.username,
        after={"channel": channel.kind},
    )
    link = f"{state.settings.public_url}/missions/{mission.id}"
    writes = " Changes wait for your approval in Z." if draft.plan.writes else ""
    flagged = (
        " (the judge flagged this message: the agent treats it as data)"
        if verdict.verdict == JudgeVerdict.flagged
        else ""
    )
    return Outcome(mission_id=mission.id, reply=f"Started “{mission.title}”{flagged}: {link}.{writes}")


async def _task(
    db: AsyncSession, state: AppState, channel: Channel, inbound: Inbound, user: User, verdict: judge.Verdict
) -> Outcome:
    seen = (
        (
            await db.execute(
                select(Task).where(
                    Task.external_ref == inbound.external_ref,
                    Task.source == ("email" if channel.kind == "email" else "chat"),
                )
            )
        ).scalar_one_or_none()
        if inbound.external_ref
        else None
    )
    if seen is not None:
        return Outcome(task_id=seen.id, reply="Already received.")
    event = {
        "from": inbound.email or inbound.username,
        "subject": inbound.subject,
        "text": inbound.text,
        "attachments": inbound.attachments,
        "judge": verdict.verdict.value,
    }
    agent, playbook = await policy.route(db, {**event, "source": channel.kind})
    task = Task(
        id=new_id("tsk"),
        source="email" if channel.kind == "email" else "chat",
        external_ref=(inbound.external_ref or "")[:255] or None,
        title=(inbound.subject or inbound.text or "Message")[:300],
        status="incoming",
        suggested_agent=agent or channel.default_agent,
        suggested_playbook=playbook,
    )
    db.add(task)
    await db.flush()
    task.body_ref = intake._store(state, task.id, event)
    await audit.record(
        db,
        audit.Actor(f"channel:{channel.id}"),
        "task.created",
        "task",
        task.id,
        runs_as=user.username,
        after={"channel": channel.kind, "judge": verdict.verdict.value},
    )
    note = "the judge held it for a human" if verdict.verdict == JudgeVerdict.blocked else "it is on the Tasks board"
    return Outcome(task_id=task.id, reply=f"Received: {note}.")


# ---------------------------------------------------------------- replies


async def reply(state: AppState, channel: Channel, inbound: Inbound, text: str) -> None:
    """Best effort: a failed reply is logged, never retried into the chat twice."""
    if not text:
        return
    resolver = state.gateway.resolver if state.gateway else SecretResolver(state.http)
    try:
        if inbound.reply_url:
            host = urlparse(inbound.reply_url).hostname or ""
            allowed = {urlparse(str((channel.config or {}).get("server_url") or "")).hostname, "hooks.slack.com"}
            allowed |= set((channel.config or {}).get("reply_hosts") or [])
            if host in allowed:
                await state.http.post(inbound.reply_url, json={"response_type": "ephemeral", "text": text}, timeout=10)
        elif inbound.teams and inbound.teams.get("service_url"):
            token = await bot_token(state, resolver, channel)
            t = inbound.teams
            await state.http.post(
                f"{t['service_url']}/v3/conversations/{t['conversation']}/activities/{t['activity']}",
                json={"type": "message", "text": text, "replyToId": t["activity"]},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        elif inbound.slack and (channel.config or {}).get("bot_token"):
            token = await _secret(resolver, channel, "bot_token")
            api = str((channel.config or {}).get("api_url") or "https://slack.com/api")
            await state.http.post(
                f"{api}/chat.postMessage",
                json={"channel": inbound.slack["channel"], "thread_ts": inbound.slack["thread_ts"], "text": text},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
    except (httpx.HTTPError, Problem) as exc:
        log.warning("reply to %s channel failed: %s", channel.kind, exc)


def channel_out(row: Channel) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "name": row.name,
        "config": row.config,
        "default_agent": row.default_agent,
        "allowed_teams": list(row.allowed_teams or []),
        "identity_mapping": row.identity_mapping,
        "enabled": row.enabled,
        "created_at": row.created_at,
    }


__all__ = ["UTC", "Agent", "datetime"]
