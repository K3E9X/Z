"""Fake Mattermost implementing packs/workplace/connectors/mattermost.openapi.json (API v4 subset), bearer auth.

Read token `ro-mattermost` reads; write token `rw-mattermost` may also post (recorded as `mattermost.posts.create`)
and delete. Posts keep `props`, as Mattermost does, so the executor's idempotency lookup can find them.
"""

import secrets
import string
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .state import Store, TokenKind

ALPHABET = string.ascii_lowercase + string.digits


def mm_id() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(26))


def mattermost_seed(now_ms: int | None = None) -> dict[str, Any]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    team = {"id": "t" * 26, "name": "secops", "display_name": "SecOps"}
    channel = {"id": "c" * 26, "team_id": team["id"], "name": "patching", "display_name": "Patching", "type": "O"}
    root = {
        "id": "p" * 26,
        "channel_id": channel["id"],
        "user_id": "u" * 26,
        "root_id": "",
        "message": "Patch window for edge-01 moved to Friday 22:00",
        "create_at": now - 3_600_000,
        "update_at": now - 3_600_000,
        "delete_at": 0,
        "props": {},
    }
    reply = {**root, "id": "r" * 26, "root_id": root["id"], "message": "ack, netops", "create_at": now - 600_000}
    reply["update_at"] = reply["create_at"]
    return {"teams": [team], "channels": [channel], "posts": [root, reply]}


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/mattermost/api/v4")
    read = store.dep("mattermost", "bearer", "read")
    write = store.dep("mattermost", "bearer", "write")

    def data() -> dict[str, Any]:
        result: dict[str, Any] = store.data["mattermost"]
        return result

    def post_list(posts: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(posts, key=lambda p: p["create_at"], reverse=True)
        return {
            "order": [p["id"] for p in ordered],
            "posts": {p["id"]: p for p in ordered},
            "next_post_id": "",
            "prev_post_id": "",
            "has_next": False,
        }

    def live(channel_id: str) -> list[dict[str, Any]]:
        return [p for p in data()["posts"] if p["channel_id"] == channel_id and not p["delete_at"]]

    @r.get("/users/me")
    def me(kind: Annotated[TokenKind, Depends(read)]) -> dict[str, Any]:
        return {"id": "z" * 26, "username": f"z-{kind}"}

    @r.get("/teams/name/{team_name}", dependencies=[Depends(read)])
    def team_by_name(team_name: str) -> dict[str, Any]:
        for team in data()["teams"]:
            if team["name"] == team_name:
                return dict(team)
        raise HTTPException(404, detail="team not found")

    @r.get("/teams/{team_id}/channels", dependencies=[Depends(read)])
    def channels(team_id: str, page: int = 0, per_page: int = 60) -> list[dict[str, Any]]:
        found = [c for c in data()["channels"] if c["team_id"] == team_id and c["type"] == "O"]
        return found[page * per_page : (page + 1) * per_page]

    @r.get("/teams/{team_id}/channels/name/{channel_name}", dependencies=[Depends(read)])
    def channel_by_name(team_id: str, channel_name: str) -> dict[str, Any]:
        for channel in data()["channels"]:
            if channel["team_id"] == team_id and channel["name"] == channel_name:
                return dict(channel)
        raise HTTPException(404, detail="channel not found")

    @r.get("/channels/{channel_id}/posts", dependencies=[Depends(read)])
    def channel_posts(channel_id: str, since: int | None = None, page: int = 0, per_page: int = 60) -> dict:
        posts = live(channel_id)
        if since is not None:
            return post_list([p for p in posts if p["update_at"] > since])
        ordered = sorted(posts, key=lambda p: p["create_at"], reverse=True)
        return post_list(ordered[page * per_page : (page + 1) * per_page])

    @r.get("/posts/{post_id}", dependencies=[Depends(read)])
    def get_post(post_id: str) -> dict[str, Any]:
        for post in data()["posts"]:
            if post["id"] == post_id and not post["delete_at"]:
                return dict(post)
        raise HTTPException(404, detail="post not found")

    @r.get("/posts/{post_id}/thread", dependencies=[Depends(read)])
    def thread(post_id: str) -> dict[str, Any]:
        root = get_post(post_id)
        root_id = root["root_id"] or root["id"]
        return post_list([p for p in data()["posts"] if root_id in (p["id"], p["root_id"]) and not p["delete_at"]])

    @r.post("/posts", status_code=201)
    async def create_post(request: Request, kind: Annotated[TokenKind, Depends(write)]) -> dict[str, Any]:
        body: dict[str, Any] = await request.json()
        channel_id, message = body.get("channel_id"), body.get("message")
        if not any(c["id"] == channel_id for c in data()["channels"]) or not message:
            raise HTTPException(400, detail="channel_id and message are required")
        if body.get("root_id") and not any(p["id"] == body["root_id"] for p in live(str(channel_id))):
            raise HTTPException(400, detail="root_id is not a post of this channel")
        now = int(time.time() * 1000)
        post = {
            "id": mm_id(),
            "channel_id": channel_id,
            "user_id": "w" * 26,
            "root_id": body.get("root_id") or "",
            "message": message,
            "create_at": now,
            "update_at": now,
            "delete_at": 0,
            "props": body.get("props") or {},
        }
        data()["posts"].append(post)
        store.record("mattermost", "mattermost.posts.create", kind, body)
        return post

    @r.delete("/posts/{post_id}")
    def delete_post(post_id: str, kind: Annotated[TokenKind, Depends(write)]) -> dict[str, str]:
        post = get_post(post_id)
        for p in data()["posts"]:
            if p["id"] == post["id"]:
                p["delete_at"] = int(time.time() * 1000)
        store.record("mattermost", "mattermost.posts.delete", kind, {"post_id": post_id})
        return {"status": "OK"}

    return r
