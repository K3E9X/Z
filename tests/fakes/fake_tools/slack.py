"""Fake Slack implementing packs/workplace/connectors/slack.openapi.json (Web API subset), bearer bot tokens.

Like Slack, every error is HTTP 200 with `{"ok": false, "error": ...}`: an unknown token gives `invalid_auth`,
the read token posting gives `missing_scope` (recorded as a denial). The write token may post (recorded as
`slack.messages.post`), delete and read history (for the executor's idempotency lookup). Messages keep their
`metadata`, returned when `include_all_metadata` is set.
"""

import time
from typing import Any

from fastapi import APIRouter, Request

from .state import Store, TokenKind, extract_token

CHANNEL = "C0Z7SECOPS"


def slack_seed(now: float | None = None) -> dict[str, Any]:
    base = int(now if now is not None else time.time())
    parent = {
        "type": "message",
        "user": "U0ALICE01",
        "text": "Edge-01 patch window moved to Friday 22:00",
        "ts": f"{base - 3600}.000100",
    }
    reply = {"type": "message", "user": "U0BOB0001", "text": "ack, netops", "ts": f"{base - 600}.000200"}
    parent["thread_ts"], reply["thread_ts"], parent["reply_count"] = parent["ts"], parent["ts"], 1
    return {
        "channels": [{"id": CHANNEL, "name": "secops-patching", "is_private": False, "is_archived": False}],
        "messages": {CHANNEL: [parent, reply]},
    }


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/slack/api")

    def data() -> dict[str, Any]:
        result: dict[str, Any] = store.data["slack"]
        return result

    def kind(request: Request, what: str, write: bool = False) -> TokenKind | dict[str, Any]:
        found = store.kind("slack", extract_token("bearer", request.headers))
        if found is None:
            store.deny("slack", what, "missing or unknown token")
            return {"ok": False, "error": "invalid_auth"}
        if write and found != "write":
            store.deny("slack", what, "read token used for a write")
            return {"ok": False, "error": "missing_scope", "needed": "chat:write"}
        return found

    def view(message: dict[str, Any], with_metadata: bool) -> dict[str, Any]:
        return {k: v for k, v in message.items() if k != "metadata" or with_metadata}

    @r.get("/conversations.list")
    def channels(request: Request) -> dict[str, Any]:
        found = kind(request, "conversations.list")
        if isinstance(found, dict):
            return found
        return {"ok": True, "channels": data()["channels"], "response_metadata": {"next_cursor": ""}}

    @r.get("/conversations.history")
    def history(request: Request, channel: str, oldest: str = "0", include_all_metadata: str = "false") -> dict:
        found = kind(request, "conversations.history")
        if isinstance(found, dict):
            return found
        if channel not in data()["messages"]:
            return {"ok": False, "error": "channel_not_found"}
        meta = include_all_metadata == "true"
        top = [m for m in data()["messages"][channel] if m.get("thread_ts") in (None, m["ts"])]
        newer = [view(m, meta) for m in top if float(m["ts"]) > float(oldest)]
        return {"ok": True, "messages": sorted(newer, key=lambda m: m["ts"], reverse=True), "has_more": False}

    @r.get("/conversations.replies")
    def replies(
        request: Request, channel: str, ts: str, oldest: str = "0", include_all_metadata: str = "false"
    ) -> dict:
        found = kind(request, "conversations.replies")
        if isinstance(found, dict):
            return found
        thread = [m for m in data()["messages"].get(channel, []) if m.get("thread_ts") == ts or m["ts"] == ts]
        if not thread:
            return {"ok": False, "error": "thread_not_found"}
        meta = include_all_metadata == "true"
        return {
            "ok": True,
            "messages": [view(m, meta) for m in thread if float(m["ts"]) >= float(oldest) or m["ts"] == ts],
        }

    @r.post("/auth.test")
    def auth_test(request: Request) -> dict[str, Any]:
        found = kind(request, "auth.test")
        if isinstance(found, dict):
            return found
        bot = "B0ZWRITE1" if found == "write" else "B0ZREAD01"
        return {"ok": True, "user_id": "U0Z" + found.upper()[:6], "bot_id": bot}

    @r.post("/chat.postMessage")
    async def post(request: Request) -> dict[str, Any]:
        found = kind(request, "chat.postMessage", write=True)
        if isinstance(found, dict):
            return found
        body: dict[str, Any] = await request.json()
        channel, text = body.get("channel"), body.get("text")
        if channel not in data()["messages"]:
            return {"ok": False, "error": "channel_not_found"}
        if not text:
            return {"ok": False, "error": "no_text"}
        message: dict[str, Any] = {"type": "message", "bot_id": "B0ZWRITE1", "text": text, "ts": f"{time.time():.6f}"}
        if body.get("thread_ts"):
            message["thread_ts"] = body["thread_ts"]
        if body.get("metadata"):
            message["metadata"] = body["metadata"]
        data()["messages"][channel].append(message)
        store.record("slack", "slack.messages.post", found, body)
        return {"ok": True, "channel": channel, "ts": message["ts"], "message": message}

    @r.post("/chat.delete")
    async def delete(request: Request) -> dict[str, Any]:
        found = kind(request, "chat.delete", write=True)
        if isinstance(found, dict):
            return found
        body: dict[str, Any] = await request.json()
        messages = data()["messages"].get(body.get("channel"), [])
        if not any(m["ts"] == body.get("ts") for m in messages):
            return {"ok": False, "error": "message_not_found"}
        data()["messages"][body["channel"]] = [m for m in messages if m["ts"] != body["ts"]]
        store.record("slack", "slack.messages.delete", found, body)
        return {"ok": True, "channel": body["channel"], "ts": body["ts"]}

    return r
