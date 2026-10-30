"""Fake Google identity platform + Gmail, Calendar, Drive (`/google`) and Chat (`/google-chat`) subsets.

Strict where Google is strict, so the connector is tested against the real rules (Google's discovery documents and
OpenID configuration):
- /oauth2/auth refuses the Microsoft-only `offline_access` scope; a refresh token is issued only with
  `access_type=offline`; refreshing never returns a new refresh token; there is no client credentials grant.
- Two clients, `read-client` / `read-secret` and `write-client` / `write-secret`; every API checks the scopes of
  the token (403 "insufficient authentication scopes"), and `gmail.metadata` alone cannot use `q` or `format=full`.
- Chat `requestId`: the same id from the same person returns the first message instead of posting again.
`POST /google/_fake/expire` invalidates every access token (to test refresh).
"""

from __future__ import annotations

import base64
import copy
import email
import hashlib
import json
import secrets
import time
from email import policy
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from .errors import FakeToolError
from .state import Store

APPS = {"read-client": "read-secret", "write-client": "write-secret"}
OPENID = {"openid", "email", "profile"}  # accepted in short form
AUTH = "https://www.googleapis.com/auth/"
USER = "alice@example.test"
SPACE = "AAAAspace01"


def google_seed() -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "mail": [
            {
                "id": "18c0a1",
                "threadId": "18c0a1",
                "labelIds": ["INBOX", "UNREAD"],
                "internalDate": str(now - 3_600_000),
                "headers": {
                    "From": "bob@example.test",
                    "To": USER,
                    "Subject": "Quarterly access review",
                    "Message-ID": "<qar-1@example.test>",
                },
                "text": "Please review the access list for the finance share by Friday.",
            },
            {
                "id": "18c0a0",
                "threadId": "18c0a0",
                "labelIds": ["SENT"],
                "internalDate": str(now - 86_400_000 * 3),
                "headers": {"From": USER, "To": "carol@example.test", "Subject": "Old mail", "Message-ID": "<o@x>"},
                "text": "Sent three days ago.",
            },
        ],
        "events": [
            {
                "id": "evt001",
                "summary": "Patch window",
                "start": {"dateTime": "2026-11-02T20:00:00+01:00"},
                "end": {"dateTime": "2026-11-02T22:00:00+01:00"},
                "creator": {"email": "netops@example.test"},
                "organizer": {"email": "netops@example.test"},
                "attendees": [{"email": USER}],
                "status": "confirmed",
            }
        ],
        "files": [
            {
                "id": "doc1",
                "name": "Roadmap 2027",
                "mimeType": "application/vnd.google-apps.document",
                "text": "Q1: SSO everywhere. Q2: retire the legacy VPN.",
            },
            {"id": "pdf1", "name": "Audit.pdf", "mimeType": "application/pdf", "text": ""},
        ],
        "spaces": [{"name": f"spaces/{SPACE}", "displayName": "SecOps", "spaceType": "SPACE"}],
        "messages": [
            {
                "name": f"spaces/{SPACE}/messages/m1",
                "text": "Who owns edge-01?",
                "sender": {"name": "users/111", "type": "HUMAN"},
                "thread": {"name": f"spaces/{SPACE}/threads/t1"},
                "createTime": "2026-10-29T08:00:00Z",
            }
        ],
    }


class GoogleError(FakeToolError):
    def __init__(self, status: int, message: str, reason: str) -> None:
        super().__init__(message, {"error": {"code": status, "message": message, "status": reason}})
        self.status = status


def _error(status: int, message: str, reason: str) -> GoogleError:
    return GoogleError(status, message, reason)


def routers(store: Store) -> list[APIRouter]:
    r = APIRouter(prefix="/google")
    chat = APIRouter(prefix="/google-chat")
    codes: dict[str, dict[str, Any]] = {}
    tokens: dict[str, dict[str, Any]] = {}
    refresh: dict[str, dict[str, Any]] = {}
    data: dict[str, Any] = google_seed()
    requests_seen: dict[str, tuple[str, dict[str, Any]]] = {}

    def reload(new: dict[str, Any]) -> None:
        data.clear()
        data.update(copy.deepcopy(new.get("google") or google_seed()))
        requests_seen.clear()

    store.on_seed.append(reload)

    # ---------------------------------------------------------------- identity platform

    @r.get("/oauth2/auth")
    def authorize(
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        scope: str,
        response_type: str = "code",
        code_challenge_method: str = "plain",
        access_type: str = "online",
        prompt: str = "",
        login_hint: str = USER,
        include_granted_scopes: str = "false",
    ) -> Response:
        scopes = scope.split()
        if client_id not in APPS or response_type != "code" or code_challenge_method != "S256":
            raise HTTPException(400, "bad authorize request")
        unknown = [s for s in scopes if not s.startswith(AUTH) and s not in OPENID]
        if unknown:  # e.g. offline_access: Error 400 invalid_scope
            raise HTTPException(400, f"invalid_scope: {' '.join(unknown)}")
        if include_granted_scopes == "true":
            raise HTTPException(400, "this fake refuses include_granted_scopes: Z must never merge grants")
        code = "code-" + secrets.token_urlsafe(12)
        codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "challenge": code_challenge,
            "scopes": set(scopes),
            "user": login_hint,
            "offline": access_type == "offline",
            "consent": "consent" in prompt.split(),
        }
        return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", status_code=302)

    def issue(grant: dict[str, Any], with_refresh: bool) -> dict[str, Any]:
        access = "ya29." + secrets.token_urlsafe(16)
        tokens[access] = grant
        body: dict[str, Any] = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": 3599,
            "scope": " ".join(sorted(grant["scopes"])),
        }
        if "openid" in grant["scopes"]:  # unsigned here: Z reads only the claims of a token-endpoint answer
            claims = {"iss": "https://accounts.google.com", "sub": "1" + grant["user"], "email": grant["user"]}
            part = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
            body["id_token"] = f"eyJhbGciOiJub25lIn0.{part}.sig"
        if with_refresh:
            rt = "1//" + secrets.token_urlsafe(16)
            refresh[rt] = grant
            body["refresh_token"] = rt
        return body

    @r.post("/oauth2/token")
    def token(
        grant_type: str = Form(...),
        client_id: str = Form(...),
        client_secret: str = Form(""),
        code: str = Form(""),
        redirect_uri: str = Form(""),
        code_verifier: str = Form(""),
        refresh_token: str = Form(""),
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
            grant = {"app": client_id, "user": pending["user"], "scopes": pending["scopes"]}
            return JSONResponse(issue(grant, with_refresh=pending["offline"]))
        if grant_type == "refresh_token":
            grant = refresh.get(refresh_token)  # Google refresh tokens are long-lived: not rotated
            if grant is None or grant["app"] != client_id:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(issue(grant, with_refresh=False))
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    @r.post("/_fake/expire")
    def expire() -> dict[str, int]:
        n = len(tokens)
        tokens.clear()
        return {"expired": n}

    def grant_of(request: Request, *scopes: str) -> dict[str, Any]:
        header = request.headers.get("authorization", "")
        grant = tokens.get(header.removeprefix("Bearer ").strip())
        if grant is None:
            raise _error(401, "Request had invalid authentication credentials.", "UNAUTHENTICATED")
        if not grant["scopes"] & {AUTH + s for s in scopes}:
            store.deny("google", request.url.path, f"{grant['app']} lacks {scopes}")
            raise _error(403, "Request had insufficient authentication scopes.", "PERMISSION_DENIED")
        return grant

    # ---------------------------------------------------------------- Gmail

    def mail_out(m: dict[str, Any], fmt: str, only: list[str] | None = None) -> dict[str, Any]:
        out = {"id": m["id"], "threadId": m["threadId"], "labelIds": m["labelIds"], "internalDate": m["internalDate"]}
        if fmt == "minimal":
            return out
        headers = [{"name": k, "value": v} for k, v in m["headers"].items() if not only or k.lower() in only]
        payload: dict[str, Any] = {"mimeType": "text/plain", "headers": headers}
        if fmt == "full":
            payload["body"] = {"data": base64.urlsafe_b64encode(m["text"].encode()).decode()}
            out["snippet"] = m["text"][:100]
        return {**out, "payload": payload}

    @r.get("/gmail/v1/users/me/messages")
    def messages_list(
        request: Request, q: str | None = None, labelIds: str | None = None, maxResults: int = 100
    ) -> dict[str, Any]:
        grant = grant_of(request, "gmail.readonly", "gmail.metadata")
        if q is not None and AUTH + "gmail.readonly" not in grant["scopes"]:
            raise _error(403, "Metadata scope does not support 'q' parameter", "PERMISSION_DENIED")
        found = sorted(data["mail"], key=lambda m: -int(m["internalDate"]))
        if labelIds:
            found = [m for m in found if labelIds in m["labelIds"]]
        if q:
            words = [w for w in q.lower().split() if ":" not in w]
            found = [m for m in found if all(w in (m["headers"]["Subject"] + m["text"]).lower() for w in words)]
        return {
            "messages": [{"id": m["id"], "threadId": m["threadId"]} for m in found[:maxResults]],
            "resultSizeEstimate": len(found),
        }

    @r.get("/gmail/v1/users/me/messages/{mid}")
    def messages_get(request: Request, mid: str, format: str = "full") -> dict[str, Any]:
        grant = grant_of(request, "gmail.readonly", "gmail.metadata")
        if format == "full" and AUTH + "gmail.readonly" not in grant["scopes"]:
            raise _error(403, "Metadata scope doesn't allow format FULL", "PERMISSION_DENIED")
        only = [h.lower() for h in request.query_params.getlist("metadataHeaders")]
        for m in data["mail"]:
            if m["id"] == mid:
                return mail_out(m, format, only if format == "metadata" else None)
        raise _error(404, "Requested entity was not found.", "NOT_FOUND")

    @r.get("/gmail/v1/users/me/threads/{tid}")
    def threads_get(request: Request, tid: str, format: str = "full") -> dict[str, Any]:
        grant_of(request, "gmail.readonly")
        mails = [mail_out(m, format) for m in data["mail"] if m["threadId"] == tid]
        if not mails:
            raise _error(404, "Requested entity was not found.", "NOT_FOUND")
        return {"id": tid, "messages": mails}

    @r.post("/gmail/v1/users/me/messages/send")
    async def messages_send(request: Request) -> dict[str, Any]:
        grant = grant_of(request, "gmail.send")
        body = await request.json()
        raw = base64.urlsafe_b64decode(str(body.get("raw", "")) + "==")
        parsed = email.message_from_bytes(raw, policy=policy.default)
        headers = {k: str(v) for k, v in parsed.items()}
        text = parsed.get_body(preferencelist=("plain",))
        mid = secrets.token_hex(8)
        mail = {
            "id": mid,
            "threadId": body.get("threadId") or mid,
            "labelIds": ["SENT"],
            "internalDate": str(int(time.time() * 1000)),
            "headers": {"From": grant["user"], **headers},
            "text": text.get_content() if text is not None else "",
        }
        data["mail"].append(mail)
        store.record("google", "google.gmail.messages.send", "write", {"as": grant["user"], **mail})
        return {"id": mid, "threadId": mail["threadId"], "labelIds": ["SENT"]}

    # ---------------------------------------------------------------- Calendar

    def event_out(e: dict[str, Any], viewer: str) -> dict[str, Any]:
        out = copy.deepcopy(e)
        for who in ("creator", "organizer"):
            if out.get(who, {}).get("email") == viewer:
                out[who]["self"] = True
        out["htmlLink"] = f"https://calendar.google.com/event?eid={e['id']}"
        return out

    @r.get("/calendar/v3/calendars/{cal}/events")
    def events_list(
        request: Request,
        cal: str,
        privateExtendedProperty: str | None = None,
        showDeleted: bool = False,
        maxResults: int = 250,
    ) -> dict[str, Any]:
        grant = grant_of(request, "calendar.readonly", "calendar.events")
        items = [e for e in data["events"] if showDeleted or e.get("status") != "cancelled"]
        if privateExtendedProperty:
            key, _, value = privateExtendedProperty.partition("=")
            items = [e for e in items if ((e.get("extendedProperties") or {}).get("private") or {}).get(key) == value]
        return {"kind": "calendar#events", "items": [event_out(e, grant["user"]) for e in items[:maxResults]]}

    @r.get("/calendar/v3/calendars/{cal}/events/{eid}")
    def events_get(request: Request, cal: str, eid: str) -> dict[str, Any]:
        grant = grant_of(request, "calendar.readonly", "calendar.events")
        for e in data["events"]:
            if e["id"] == eid:
                return event_out(e, grant["user"])
        raise _error(404, "Not Found", "NOT_FOUND")

    @r.post("/calendar/v3/calendars/{cal}/events")
    async def events_insert(request: Request, cal: str, sendUpdates: str = "none") -> dict[str, Any]:
        grant = grant_of(request, "calendar.events")
        body = await request.json()
        if not body.get("start") or not body.get("end"):
            raise _error(400, "Missing end time.", "INVALID_ARGUMENT")
        event = {
            **body,
            "id": secrets.token_hex(10),
            "creator": {"email": grant["user"]},
            "organizer": {"email": grant["user"]},
            "status": "confirmed",
        }
        data["events"].append(event)
        store.record(
            "google",
            "google.calendar.events.insert",
            "write",
            {"as": grant["user"], "calendar": cal, "sendUpdates": sendUpdates, **event},
        )
        return event_out(event, grant["user"])

    # ---------------------------------------------------------------- Drive

    def file_out(f: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "drive#file", "id": f["id"], "name": f["name"], "mimeType": f["mimeType"]}

    @r.get("/drive/v3/files")
    def files_list(request: Request, q: str | None = None, pageSize: int = 100) -> dict[str, Any]:
        grant_of(request, "drive.readonly")
        found = data["files"]
        if q and "contains" in q:
            needle = q.split("contains", 1)[1].split("'")[1].lower()
            found = [f for f in found if needle in f["name"].lower()]
        return {"files": [file_out(f) for f in found[:pageSize]]}

    @r.get("/drive/v3/files/{fid}")
    def files_get(request: Request, fid: str) -> dict[str, Any]:
        grant_of(request, "drive.readonly")
        for f in data["files"]:
            if f["id"] == fid:
                return file_out(f)
        raise _error(404, f"File not found: {fid}.", "NOT_FOUND")

    @r.get("/drive/v3/files/{fid}/export")
    def files_export(request: Request, fid: str, mimeType: str) -> Response:
        grant_of(request, "drive.readonly")
        for f in data["files"]:
            if f["id"] == fid:
                if not f["mimeType"].startswith("application/vnd.google-apps."):
                    raise _error(403, "Export only supports Docs Editors files.", "PERMISSION_DENIED")
                return PlainTextResponse(f["text"], media_type=mimeType)
        raise _error(404, f"File not found: {fid}.", "NOT_FOUND")

    # ---------------------------------------------------------------- Chat

    @chat.get("/v1/spaces")
    def spaces_list(request: Request) -> dict[str, Any]:
        grant_of(request, "chat.spaces.readonly")
        return {"spaces": data["spaces"]}

    @chat.get("/v1/spaces/{space}/messages")
    def chat_messages(request: Request, space: str) -> dict[str, Any]:
        grant_of(request, "chat.messages.readonly")
        return {"messages": [m for m in data["messages"] if m["name"].startswith(f"spaces/{space}/")]}

    @chat.post("/v1/spaces/{space}/messages")
    async def chat_post(
        request: Request, space: str, requestId: str | None = None, messageReplyOption: str | None = None
    ) -> dict[str, Any]:
        grant = grant_of(request, "chat.messages.create", "chat.messages")
        if not any(s["name"] == f"spaces/{space}" for s in data["spaces"]):
            raise _error(404, "Space not found.", "NOT_FOUND")
        if requestId and requestId in requests_seen:
            who, first = requests_seen[requestId]
            if who != grant["user"]:
                raise _error(400, "Request id reused by another user.", "INVALID_ARGUMENT")
            return first
        body = await request.json()
        thread = (body.get("thread") or {}).get("name") if messageReplyOption else None
        message = {
            "name": f"spaces/{space}/messages/{secrets.token_hex(6)}",
            "text": body.get("text", ""),
            "sender": {"name": "users/999", "type": "HUMAN"},
            "thread": {"name": thread or f"spaces/{space}/threads/{secrets.token_hex(4)}"},
            "createTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        data["messages"].append(message)
        if requestId:
            requests_seen[requestId] = (grant["user"], message)
        store.record("google", "gchat.messages.create", "write", {"as": grant["user"], **message})
        return message

    return [r, chat]
