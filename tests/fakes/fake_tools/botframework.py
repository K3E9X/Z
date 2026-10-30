"""Fake Bot Framework: OpenID metadata + JWKS for the tokens it signs, the bot token endpoint, conversation
members (with e-mail and UPN) and reply activities (recorded as writes "teams.reply").

`POST /botframework/_fake/sign {claims}` returns a Bot Connector token signed with the fake's RSA key, so tests
send Teams activities exactly like the Bot Connector does.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Form, HTTPException, Request

from .state import Store

KID = "fake-bf-key"
APP_ID = "z-bot-app-id"
APP_PASSWORD = "z-bot-password"


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/botframework")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    issued: set[str] = set()

    @r.get("/openid")
    def metadata(request: Request) -> dict[str, Any]:
        return {"issuer": "https://api.botframework.com", "jwks_uri": str(request.base_url) + "botframework/jwks"}

    @r.get("/jwks")
    def jwks() -> dict[str, Any]:
        return {
            "keys": [
                {"kty": "RSA", "use": "sig", "kid": KID, "alg": "RS256", "n": _b64(numbers.n), "e": _b64(numbers.e)}
            ]
        }

    @r.post("/_fake/sign")
    async def sign(request: Request) -> dict[str, str]:
        claims = {
            "iss": "https://api.botframework.com",
            "aud": APP_ID,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
            **(await request.json()),
        }
        return {"token": jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})}

    @r.post("/token")
    def token(
        client_id: str = Form(...), client_secret: str = Form(...), grant_type: str = Form(...), scope: str = Form("")
    ) -> dict[str, Any]:
        if client_id != APP_ID or client_secret != APP_PASSWORD or grant_type != "client_credentials":
            raise HTTPException(401, "bad bot credentials")
        value = f"bot-{len(issued)}"
        issued.add(value)
        return {"access_token": value, "token_type": "Bearer", "expires_in": 3600}

    def bot(request: Request) -> None:
        if request.headers.get("authorization", "").removeprefix("Bearer ") not in issued:
            raise HTTPException(401, "bot token required")

    @r.get("/v3/conversations/{conversation}/members/{member}")
    def member(conversation: str, member: str, request: Request) -> dict[str, Any]:
        bot(request)
        name = member.removeprefix("29:")
        return {
            "id": member,
            "name": name.title(),
            "email": f"{name}@example.test",
            "userPrincipalName": f"{name}@corp.example.test",
        }

    @r.post("/v3/conversations/{conversation}/activities/{activity}")
    async def reply(conversation: str, activity: str, request: Request) -> dict[str, str]:
        bot(request)
        store.record(
            "teams",
            "teams.reply",
            "write",
            {"conversation": conversation, "reply_to": activity, **(await request.json())},
        )
        return {"id": f"reply-{activity}"}

    return r
