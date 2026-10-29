"""Fake OAuth authorization server + a small Graph-like mail API, for delegated (user) and app-only grants.

Two app registrations, as in docs/05 §6: `read-client` / `read-secret` (read scopes) and `write-client` /
`write-secret` (write scopes). /authorize consents at once for `login_hint` (default alice@example.test) and
redirects back with a code; /token checks PKCE (S256), the client secret and the redirect URI, and issues
access + refresh tokens bound to the app; client_credentials gives app-only tokens. The mail API accepts only
tokens of the right app: reads with the read app, sends with the write app (invariant 7 in the fake too).
`POST /m365/_fake/expire` makes every issued access token invalid (to test refresh).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .state import Store

APPS = {"read-client": "read-secret", "write-client": "write-secret"}
MAILBOX = [
    {"id": "m1", "subject": "Quarterly access review", "from": "bob@example.test", "isRead": False},
    {"id": "m2", "subject": "Patch window on Friday", "from": "carol@example.test", "isRead": True},
]


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/m365")
    codes: dict[str, dict[str, Any]] = {}
    tokens: dict[str, dict[str, Any]] = {}  # access token -> grant
    refresh: dict[str, dict[str, Any]] = {}

    def issue(grant: dict[str, Any], with_refresh: bool = True) -> dict[str, Any]:
        access = "at-" + secrets.token_urlsafe(16)
        tokens[access] = grant
        body: dict[str, Any] = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": grant["scope"],
        }
        if with_refresh:
            rt = "rt-" + secrets.token_urlsafe(16)
            refresh[rt] = grant
            body["refresh_token"] = rt
        return body

    @r.get("/oauth2/authorize")
    def authorize(
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scope: str = "",
        login_hint: str = "alice@example.test",
        code_challenge_method: str = "S256",
        response_type: str = "code",
        prompt: str = "",
    ) -> Response:
        if client_id not in APPS or response_type != "code" or code_challenge_method != "S256":
            raise HTTPException(400, "bad authorize request")
        code = "code-" + secrets.token_urlsafe(12)
        codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "challenge": code_challenge,
            "scope": scope,
            "user": login_hint,
        }
        return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", status_code=302)

    @r.post("/oauth2/token")
    def token(
        grant_type: str = Form(...),
        client_id: str = Form(...),
        client_secret: str = Form(""),
        code: str = Form(""),
        redirect_uri: str = Form(""),
        code_verifier: str = Form(""),
        refresh_token: str = Form(""),
        scope: str = Form(""),
    ) -> JSONResponse:
        if APPS.get(client_id) != client_secret:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        if grant_type == "authorization_code":
            pending = codes.pop(code, None)
            if pending is None or pending["client_id"] != client_id or pending["redirect_uri"] != redirect_uri:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            digest = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
            if digest != pending["challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE mismatch"}, status_code=400)
            return JSONResponse(issue({"app": client_id, "user": pending["user"], "scope": pending["scope"]}))
        if grant_type == "refresh_token":
            grant = refresh.pop(refresh_token, None)
            if grant is None or grant["app"] != client_id:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(issue(grant))
        if grant_type == "client_credentials":
            return JSONResponse(issue({"app": client_id, "user": None, "scope": scope}, with_refresh=False))
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    @r.post("/_fake/expire")
    def expire() -> dict[str, int]:
        n = len(tokens)
        tokens.clear()
        return {"expired": n}

    def grant_of(request: Request, app: str) -> dict[str, Any]:
        header = request.headers.get("authorization", "")
        grant = tokens.get(header.removeprefix("Bearer ").strip())
        if grant is None:
            raise HTTPException(401, "invalid token")
        if grant["app"] != app:
            store.deny("m365", request.url.path, f"token of {grant['app']} used where {app} is required")
            raise HTTPException(403, "wrong app for this operation")
        return grant

    @r.get("/v1.0/me/messages")
    def my_messages(request: Request) -> dict[str, Any]:
        grant = grant_of(request, "read-client")
        if grant["user"] is None:
            raise HTTPException(400, "/me needs a delegated token")
        return {"value": [{**m, "owner": grant["user"]} for m in MAILBOX]}

    @r.get("/v1.0/users/{user}/messages")
    def user_messages(user: str, request: Request) -> dict[str, Any]:
        grant_of(request, "read-client")
        return {"value": [{**m, "owner": user} for m in MAILBOX]}

    events: dict[str, dict[str, Any]] = {}  # transactionId -> event (Graph does not create it twice)

    @r.post("/v1.0/me/events", status_code=201)
    async def create_event(request: Request) -> dict[str, Any]:
        grant = grant_of(request, "write-client")
        body = await request.json()
        tx = body.get("transactionId")
        if tx and tx in events:
            return events[tx]
        event = {**body, "id": "AAMkE" + secrets.token_hex(6), "webLink": "https://outlook.office.com/calendar/item"}
        if tx:
            events[tx] = event
        store.record("m365", "m365.calendar.events.create", "write", {"as": grant["user"], **event})
        return event

    @r.post("/v1.0/me/sendMail", status_code=202)
    async def send_mail(request: Request) -> Response:
        grant = grant_of(request, "write-client")
        body = await request.json()
        store.record("m365", "m365.mail.send", "write", {"as": grant["user"], **body})
        return Response(status_code=202)

    return r
