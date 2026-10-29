"""Follow-ups in the chat a mission was started from (docs/05 §10, ADR 0017).

When a chat message starts a mission, the reply target (Teams conversation, Slack thread, or a slash-command
response URL) is sealed into a `channel_threads` row. A worker walks each open thread's mission events after
its cursor and posts what the person needs to act on:
- an action waiting for approval: a card (Teams) or message with a link to its review in Z. Approving always
  happens in Z, behind the Z session (and step-up when required): a chat button never approves, and
  destructive or two-approver actions say so;
- a question from the agent, with a link to answer it;
- a job needing attention (judge hold, connector down, ...) or the mission ending.
Content (summaries, questions) is posted only up to the channel's `content_up_to` data class (default C1);
above it the message says the details are in Z. Delivery is at least once: a crash after a post and before
the commit may repeat that one post. A thread whose posts keep failing is marked failed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import DataClass, JobStatus, MissionStatus

from z_server import channels
from z_server.db.models import Channel, ChannelThread, Event, Mission
from z_server.errors import Problem
from z_server.gateway.secrets import SecretResolver
from z_server.ids import new_id
from z_server.state import AppState

log = logging.getLogger("z.channels")
MAX_FAILURES = 5
BATCH_THREADS = 50
BATCH_EVENTS = 50
ATTENTION = {JobStatus.needs_attention.value}


def follow(db: AsyncSession, state: AppState, channel: Channel, mission_id: str, inbound: channels.Inbound) -> None:
    target = {"teams": inbound.teams, "slack": inbound.slack, "reply_url": inbound.reply_url}
    if not any(target.values()):
        return
    thread_id = new_id("cth")
    db.add(
        ChannelThread(
            id=thread_id,
            channel_id=channel.id,
            mission_id=mission_id,
            target_enc=state.sealer.seal(json.dumps(target), f"thread:{thread_id}"),
            last_seq=-1,
            status="open",
            failures=0,
        )
    )


def _shown(channel: Channel, mission: Mission) -> bool:
    limit = str((channel.config or {}).get("content_up_to") or "C1")
    try:
        return DataClass(mission.data_class).rank <= DataClass(limit).rank
    except ValueError:
        return False


def message(state: AppState, channel: Channel, mission: Mission, event: Event) -> dict[str, Any] | None:
    """{title, text, link, link_label, facts} for an event worth a chat post, else None."""
    base = state.settings.public_url
    p = event.payload or {}
    hidden = f"Details are in Z (data class {mission.data_class})."
    if event.type == "action_proposed":
        destructive = p.get("class") == "destructive"
        return {
            "title": "Approval needed",
            "text": str(p.get("summary") or "") if _shown(channel, mission) else hidden,
            "facts": [("Operation", str(p.get("operation"))), ("Class", str(p.get("class")))],
            "note": "Destructive: two approvers, in the Z app only." if destructive else "Approve or reject in Z.",
            "link": f"{base}/review?action={p.get('action_id')}",
            "link_label": "Review in Z",
        }
    if event.type == "question":
        return {
            "title": f"{event.agent or 'The agent'} asks",
            "text": str(p.get("text") or "") if _shown(channel, mission) else hidden,
            "facts": [],
            "note": "Answer in Z.",
            "link": f"{base}/missions/{mission.id}",
            "link_label": "Answer in Z",
        }
    if event.type == "status" and p.get("entity") in {"mission", "job"}:
        to = str(p.get("to") or "")
        attention = p.get("entity") == "job" and to in ATTENTION
        ended = p.get("entity") == "mission" and to in MissionStatus.__members__ and MissionStatus(to).terminal
        if not (attention or ended):
            return None
        return {
            "title": "Needs attention" if attention else f"Mission {to.replace('_', ' ')}",
            "text": mission.title if _shown(channel, mission) else hidden,
            "facts": [("Reason", str(p["reason"]))] if p.get("reason") and _shown(channel, mission) else [],
            "note": "",
            "link": f"{base}/missions/{mission.id}",
            "link_label": "Open in Z",
        }
    return None


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def plain(msg: dict[str, Any], markdown_links: bool) -> str:
    facts = " · ".join(f"{k}: {v}" for k, v in msg["facts"])
    link = f"[{msg['link_label']}]({msg['link']})" if markdown_links else f"<{msg['link']}|{msg['link_label']}>"
    lines = [f"*{msg['title']}*", msg["text"], facts, msg["note"], link]
    return "\n".join(line for line in lines if line)


def adaptive_card(msg: dict[str, Any]) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": msg["title"], "weight": "Bolder", "size": "Medium", "wrap": True},
        {"type": "TextBlock", "text": msg["text"], "wrap": True},
    ]
    if msg["facts"]:
        body.append({"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in msg["facts"]]})
    if msg["note"]:
        body.append({"type": "TextBlock", "text": msg["note"], "isSubtle": True, "wrap": True})
    return {
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": body,
            "actions": [{"type": "Action.OpenUrl", "title": msg["link_label"], "url": msg["link"]}],
        },
    }


async def post(state: AppState, channel: Channel, target: dict[str, Any], msg: dict[str, Any]) -> bool:
    resolver = state.gateway.resolver if state.gateway else SecretResolver(state.http)
    try:
        if target.get("teams"):
            t = target["teams"]
            token = await channels.bot_token(state, resolver, channel)
            resp = await state.http.post(
                f"{t['service_url']}/v3/conversations/{t['conversation']}/activities/{t['activity']}",
                json={"type": "message", "replyToId": t["activity"], "attachments": [adaptive_card(msg)]},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        elif target.get("slack") and (channel.config or {}).get("bot_token"):
            s = target["slack"]
            token = await channels.channel_secret(resolver, channel, "bot_token")
            api = str((channel.config or {}).get("api_url") or "https://slack.com/api")
            escaped = {**msg, "title": _slack_escape(msg["title"]), "text": _slack_escape(msg["text"])}
            resp = await state.http.post(
                f"{api}/chat.postMessage",
                json={"channel": s["channel"], "thread_ts": s["thread_ts"], "text": plain(escaped, False)},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if resp.status_code == 200 and not resp.json().get("ok", False):
                return False
        elif target.get("reply_url") and channels.reply_url_allowed(channel, str(target["reply_url"])):
            mattermost = channel.kind == "mattermost"
            text = plain(msg if mattermost else {**msg, "text": _slack_escape(msg["text"])}, mattermost)
            resp = await state.http.post(
                str(target["reply_url"]), json={"response_type": "ephemeral", "text": text}, timeout=10
            )
        else:
            return False
    except (httpx.HTTPError, Problem, ValueError) as exc:
        log.warning("follow-up to %s channel failed: %s", channel.kind, exc)
        return False
    return 200 <= resp.status_code < 300


async def tick(state: AppState) -> dict[str, list[str]]:
    report: dict[str, list[str]] = {"posted": [], "closed": [], "failed": []}
    async with state.sessions() as db, db.begin():
        threads = (
            (
                await db.execute(
                    select(ChannelThread)
                    .where(ChannelThread.status == "open")
                    .order_by(ChannelThread.updated_at)
                    .limit(BATCH_THREADS)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for thread in threads:
            channel = await db.get(Channel, thread.channel_id)
            mission = await db.get(Mission, thread.mission_id)
            if channel is None or mission is None or not channel.enabled:
                thread.status = "closed"
                report["closed"].append(thread.id)
                continue
            target = json.loads(state.sealer.open(thread.target_enc, f"thread:{thread.id}"))
            pending = (
                (
                    await db.execute(
                        select(Event)
                        .where(Event.mission_id == mission.id, Event.seq > thread.last_seq)
                        .order_by(Event.seq)
                        .limit(BATCH_EVENTS)
                    )
                )
                .scalars()
                .all()
            )
            for event in pending:
                msg = message(state, channel, mission, event)
                if msg is not None:
                    if not await post(state, channel, target, msg):
                        thread.failures += 1
                        if thread.failures >= MAX_FAILURES:
                            thread.status = "failed"
                            report["failed"].append(thread.id)
                        break
                    report["posted"].append(f"{thread.id}:{event.seq}")
                thread.last_seq = event.seq
            else:
                if MissionStatus(mission.status).terminal and len(pending) < BATCH_EVENTS:
                    thread.status = "closed"
                    report["closed"].append(thread.id)
    return report
