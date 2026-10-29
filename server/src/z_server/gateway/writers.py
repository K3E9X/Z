"""Connector-specific write adapters for the action executor (docs/05 §2 "action executor").

An adapter turns the reviewed payload (models/actions.py built-ins) into the tool's API calls, and can look up
whether an earlier attempt already applied the action (idempotency after a crash). Operations without an
adapter use the generic path: the operation's own request with the payload as arguments.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from typing import Any, Protocol
from urllib.parse import quote

from z_common.redaction import Redactor

from z_server.db.models import Connector, Operation, ProposedAction
from z_server.gateway import adf, git
from z_server.gateway import http as http_invoker
from z_server.gateway.auth import material
from z_server.gateway.core import Gateway, Invocation


class WriteError(RuntimeError):
    pass


@dataclass
class Applied:
    invocation: Invocation
    result: dict[str, Any]


class Writer(Protocol):
    async def lookup(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction
    ) -> dict[str, Any] | None: ...

    async def apply(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction, files: dict[str, bytes]
    ) -> Applied: ...


def action_label(action: ProposedAction) -> str:
    return f"z-{action.id.lower()}"


def _fail(inv: Invocation, what: str) -> None:
    if not inv.ok:
        detail = inv.body if isinstance(inv.body, dict | str) else None
        message = f"{what}: {inv.error}" + (f" {str(detail)[:500]}" if detail else "")
        raise WriteError(Redactor(inv.secrets).text(message))  # the action result and the audit quote it


class JiraCloud:
    """Jira Cloud REST v3. Issues carry a z-<action> label and comments a `z` property for idempotency."""

    def _browse(self, gw: Gateway, connector: Connector, key: str) -> str:
        return f"{http_invoker.base_url(connector.base_url, connector.manifest)}/browse/{key}"

    async def _account_id(self, gw: Gateway, connector: Connector, who: str) -> str:
        if ":" in who and " " not in who:  # already an accountId (e.g. 557058:...)
            return who
        inv = await gw.send(connector, "GET", "/rest/api/3/user/search", "write", params={"query": who})
        _fail(inv, "assignee lookup")
        users = inv.body if isinstance(inv.body, list) else []
        exact = [
            u
            for u in users
            if who.lower() in {str(u.get("emailAddress", "")).lower(), str(u.get("displayName", "")).lower()}
        ]
        chosen = exact or (users if len(users) == 1 else [])
        if not chosen:
            raise WriteError(f"assignee {who!r} matches {len(users)} Jira users; give an e-mail or accountId")
        return str(chosen[0]["accountId"])

    async def _fields(self, gw: Gateway, connector: Connector, fields: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, value in fields.items():
            if name in {"description", "environment"} and isinstance(value, str):
                out[name] = adf.to_adf(value)
            elif name in {"priority", "issuetype", "resolution"} and isinstance(value, str):
                out[name] = {"name": value}
            elif name == "assignee" and isinstance(value, str):
                out[name] = {"accountId": await self._account_id(gw, connector, value)}
            elif name == "components" and isinstance(value, list):
                out[name] = [{"name": c} if isinstance(c, str) else c for c in value]
            elif name in {"due_date", "duedate"}:
                out["duedate"] = value
            else:
                out[name] = value
        return out

    async def lookup(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction
    ) -> dict[str, Any] | None:
        if op.local_id == "issues.create":
            inv = await gw.send(
                connector,
                "GET",
                "/rest/api/3/search/jql",
                "write",
                params={"jql": f'labels = "{action_label(action)}"', "fields": "summary", "maxResults": 1},
            )
            _fail(inv, "idempotency lookup")
            issues = (inv.body or {}).get("issues") or []
            if issues:
                key = issues[0]["key"]
                return {
                    "key": key,
                    "id": issues[0].get("id"),
                    "url": self._browse(gw, connector, key),
                    "recovered": True,
                }
            return None
        if op.local_id == "comments.create":
            issue = action.payload["issue"]
            inv = await gw.send(
                connector,
                "GET",
                f"/rest/api/3/issue/{issue}/comment",
                "write",
                params={"expand": "properties", "maxResults": 100},
            )
            _fail(inv, "idempotency lookup")
            for comment in (inv.body or {}).get("comments") or []:
                for prop in comment.get("properties") or []:
                    if prop.get("key") == "z" and (prop.get("value") or {}).get("action") == action.id:
                        return {"issue": issue, "comment_id": comment["id"], "recovered": True}
            return None
        return None  # updates and transitions are idempotent: applying again is safe

    async def apply(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction, files: dict[str, bytes]
    ) -> Applied:
        p = action.payload
        if op.local_id == "issues.create":
            fields = await self._fields(
                gw,
                connector,
                {
                    "project": {"key": p["project"]},
                    "summary": p["summary"],
                    "issuetype": p.get("issue_type") or "Task",
                    "description": p.get("description") or "",
                    "labels": sorted({*(p.get("labels") or []), action_label(action)}),
                    **({"priority": p["priority"]} if p.get("priority") else {}),
                    **({"assignee": p["assignee"]} if p.get("assignee") else {}),
                    **({"duedate": p["due_date"]} if p.get("due_date") else {}),
                    **({"components": p["components"]} if p.get("components") else {}),
                },
            )
            inv = await gw.send(connector, "POST", "/rest/api/3/issue", "write", json_body={"fields": fields})
            _fail(inv, "create issue")
            key = inv.body["key"]
            return Applied(inv, {"key": key, "id": inv.body.get("id"), "url": self._browse(gw, connector, key)})
        if op.local_id == "issues.update":
            fields = await self._fields(gw, connector, dict(p["fields"]))
            inv = await gw.send(
                connector, "PUT", f"/rest/api/3/issue/{p['issue']}", "write", json_body={"fields": fields}
            )
            _fail(inv, "update issue")
            return Applied(
                inv, {"key": p["issue"], "updated": sorted(fields), "url": self._browse(gw, connector, p["issue"])}
            )
        if op.local_id == "comments.create":
            body = {"body": adf.to_adf(p["body"]), "properties": [{"key": "z", "value": {"action": action.id}}]}
            inv = await gw.send(connector, "POST", f"/rest/api/3/issue/{p['issue']}/comment", "write", json_body=body)
            _fail(inv, "add comment")
            return Applied(
                inv,
                {"issue": p["issue"], "comment_id": inv.body.get("id"), "url": self._browse(gw, connector, p["issue"])},
            )
        return await Generic().apply(gw, connector, op, action, files)


def marker(action: ProposedAction) -> str:
    return f"<!-- z-action:{action.id} -->"


class GitLab:
    """GitLab REST v4 + git. Issues and notes carry an invisible `z-action` marker; a merge request is found
    again by its z/* source branch. The patch is pushed with the write token by the git helper."""

    def _project(self, project: str) -> str:
        return quote(str(project), safe="")

    async def _user_id(self, gw: Gateway, connector: Connector, username: str) -> int:
        inv = await gw.send(connector, "GET", "/api/v4/users", "write", params={"username": username.lstrip("@")})
        _fail(inv, "assignee lookup")
        users = inv.body if isinstance(inv.body, list) else []
        if len(users) != 1:
            raise WriteError(f"assignee {username!r} is not a single GitLab user")
        return int(users[0]["id"])

    async def lookup(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction
    ) -> dict[str, Any] | None:
        p = action.payload
        project = self._project(p["project"])
        if op.local_id == "merge_requests.create":
            inv = await gw.send(
                connector,
                "GET",
                f"/api/v4/projects/{project}/merge_requests",
                "write",
                params={"state": "opened", "source_branch": p["source_branch"]},
            )
            _fail(inv, "idempotency lookup")
            for mr in inv.body or []:
                if mr.get("source_branch") == p["source_branch"]:
                    return {"iid": mr["iid"], "url": mr.get("web_url"), "recovered": True}
            return None
        if op.local_id == "issues.create":
            inv = await gw.send(
                connector,
                "GET",
                f"/api/v4/projects/{project}/issues",
                "write",
                params={"search": f"z-action:{action.id}", "in": "description", "state": "all"},
            )
            _fail(inv, "idempotency lookup")
            for issue in inv.body or []:
                if marker(action) in (issue.get("description") or ""):
                    return {"iid": issue["iid"], "url": issue.get("web_url"), "recovered": True}
            return None
        if op.local_id == "notes.create":
            kind, iid = (
                ("merge_requests", p["merge_request_iid"]) if p.get("merge_request_iid") else ("issues", p["issue_iid"])
            )
            inv = await gw.send(
                connector, "GET", f"/api/v4/projects/{project}/{kind}/{iid}/notes", "write", params={"per_page": 100}
            )
            _fail(inv, "idempotency lookup")
            for note in inv.body or []:
                if marker(action) in (note.get("body") or ""):
                    return {"note_id": note["id"], "recovered": True}
            return None
        return None

    async def apply(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction, files: dict[str, bytes]
    ) -> Applied:
        p = action.payload
        project = self._project(p["project"])
        if op.local_id == "issues.create":
            body: dict[str, Any] = {
                "title": p["title"],
                "description": f"{p.get('description') or ''}\n\n{marker(action)}",
            }
            if p.get("labels"):
                body["labels"] = ",".join(p["labels"])
            if p.get("due_date"):
                body["due_date"] = p["due_date"]
            if p.get("assignee"):
                body["assignee_ids"] = [await self._user_id(gw, connector, p["assignee"])]
            inv = await gw.send(connector, "POST", f"/api/v4/projects/{project}/issues", "write", json_body=body)
            _fail(inv, "create issue")
            return Applied(inv, {"iid": inv.body["iid"], "url": inv.body.get("web_url")})
        if op.local_id == "notes.create":
            kind, iid = (
                ("merge_requests", p["merge_request_iid"])
                if p.get("merge_request_iid")
                else ("issues", p.get("issue_iid"))
            )
            if iid is None:
                raise WriteError("a note needs merge_request_iid or issue_iid")
            inv = await gw.send(
                connector,
                "POST",
                f"/api/v4/projects/{project}/{kind}/{iid}/notes",
                "write",
                json_body={"body": f"{p['body']}\n\n{marker(action)}"},
            )
            _fail(inv, "add note")
            return Applied(
                inv,
                {
                    "note_id": inv.body.get("id"),
                    "target": f"{p['project']}{'!' if kind == 'merge_requests' else '#'}{iid}",
                },
            )
        if op.local_id == "merge_requests.create":
            patch = files.get("patch_artifact")
            if not patch:
                raise WriteError("the patch artifact is missing")
            auth = await material(connector.manifest, "write", gw.resolver)
            token = auth.secrets[0] if auth.secrets else None
            if not token:
                raise WriteError("the GitLab write credential is unavailable")
            base = http_invoker.base_url(connector.base_url, connector.manifest)
            try:
                sha = await git.push_patch(
                    base,
                    p["project"],
                    p["target_branch"],
                    p["source_branch"],
                    bytes(patch),
                    f"{p['title']}\n\nProposed by Z agent {action.agent}, approved in Z ({action.id}).",
                    token,
                    ("Z", "z-bot@noreply.invalid"),
                )
            except git.GitError as exc:
                raise WriteError(str(exc)) from exc
            body = {
                "source_branch": p["source_branch"],
                "target_branch": p["target_branch"],
                "title": p["title"],
                "description": f"{p.get('description') or ''}\n\n{marker(action)}",
                "remove_source_branch": True,
            }
            if p.get("labels"):
                body["labels"] = ",".join(p["labels"])
            inv = await gw.send(
                connector, "POST", f"/api/v4/projects/{project}/merge_requests", "write", json_body=body
            )
            _fail(inv, "create merge request")
            return Applied(
                inv,
                {
                    "iid": inv.body["iid"],
                    "url": inv.body.get("web_url"),
                    "commit": sha,
                    "source_branch": p["source_branch"],
                },
            )
        return await Generic().apply(gw, connector, op, action, files)


class Mattermost:
    """Mattermost API v4. A post carries `props.z_action` (the agent cannot set props: the operation's body schema
    has no such field); after a crash the channel's posts since the proposal are searched for it."""

    async def lookup(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction
    ) -> dict[str, Any] | None:
        if op.local_id != "posts.create":
            return None
        body = action.payload.get("body") or {}
        since = int(action.created_at.timestamp() * 1000) - 60_000  # tolerate clock skew with the server
        inv = await gw.send(
            connector,
            "GET",
            f"/api/v4/channels/{quote(str(body.get('channel_id', '')), safe='')}/posts",
            "write",
            params={"since": since},
        )
        _fail(inv, "idempotency lookup")
        posts = (inv.body or {}).get("posts") if isinstance(inv.body, dict) else None
        marked = [
            p
            for p in (posts or {}).values()
            if isinstance(p, dict) and (p.get("props") or {}).get("z_action") == action.id
        ]
        if not marked:
            return None
        # any channel member can set props: only a post by the write account counts as already applied
        me = await gw.send(connector, "GET", "/api/v4/users/me", "write")
        _fail(me, "idempotency lookup (write account)")
        mine = [p for p in marked if p.get("user_id") == (me.body or {}).get("id")]
        if mine:
            return {"id": mine[0].get("id"), "channel_id": mine[0].get("channel_id"), "recovered": True}
        return None

    async def apply(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction, files: dict[str, bytes]
    ) -> Applied:
        if op.local_id != "posts.create":
            return await Generic().apply(gw, connector, op, action, files)
        body = {**(action.payload.get("body") or {}), "props": {"z_action": action.id}}
        inv = await gw.send(connector, "POST", "/api/v4/posts", "write", json_body=body)
        _fail(inv, "post")
        post = inv.body if isinstance(inv.body, dict) else {}
        return Applied(
            inv,
            {
                "status": inv.status,
                "id": post.get("id"),
                "channel_id": post.get("channel_id"),
                **({"root_id": post["root_id"]} if post.get("root_id") else {}),
            },
        )


class Slack:
    """Slack Web API. A message carries metadata `{event_type: z_action, event_payload: {action}}` (the agent cannot
    set metadata: the operation's body schema has no such field); after a crash the channel, or the thread, is
    read since the proposal and searched for it. Slack's `ok: false` answers fail through the manifest's
    `error_body`, so a refused post is never recorded as applied."""

    async def lookup(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction
    ) -> dict[str, Any] | None:
        if op.local_id != "messages.post":
            return None
        body = action.payload.get("body") or {}
        params: dict[str, Any] = {
            "channel": body.get("channel"),
            "oldest": f"{action.created_at.timestamp() - 60:.6f}",  # tolerate clock skew with Slack
            "include_all_metadata": "true",
            "limit": 200,
        }
        path = "/conversations.history"
        if body.get("thread_ts"):
            path, params["ts"] = "/conversations.replies", body["thread_ts"]
        inv = await gw.send(connector, "GET", path, "write", params=params)
        _fail(inv, "idempotency lookup")
        marked = []
        for message in (inv.body or {}).get("messages") or [] if isinstance(inv.body, dict) else []:
            meta = message.get("metadata") or {} if isinstance(message, dict) else {}
            if meta.get("event_type") == "z_action" and (meta.get("event_payload") or {}).get("action") == action.id:
                marked.append(message)
        if not marked:
            return None
        # another app in the channel could post the same metadata: only the write app's own message counts
        me = await gw.send(connector, "POST", "/auth.test", "write", json_body={})
        _fail(me, "idempotency lookup (write app)")
        bot = (me.body or {}).get("bot_id") if isinstance(me.body, dict) else None
        mine = [m for m in marked if bot and m.get("bot_id") == bot]
        if mine:
            return {"channel": body.get("channel"), "ts": mine[0].get("ts"), "recovered": True}
        return None

    async def apply(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction, files: dict[str, bytes]
    ) -> Applied:
        if op.local_id != "messages.post":
            return await Generic().apply(gw, connector, op, action, files)
        body = {
            **(action.payload.get("body") or {}),
            "metadata": {"event_type": "z_action", "event_payload": {"action": action.id}},
        }
        inv = await gw.send(connector, "POST", "/chat.postMessage", "write", json_body=body)
        _fail(inv, "post")
        answer = inv.body if isinstance(inv.body, dict) else {}
        return Applied(
            inv,
            {
                "status": inv.status,
                "channel": answer.get("channel"),
                "ts": answer.get("ts"),
                **({"thread_ts": body["thread_ts"]} if body.get("thread_ts") else {}),
            },
        )


MAIL_MARKER = "X-Z-Action"
SENT_SCAN = 25  # recent sent mails read again after a crash (the executor retries within minutes, not days)


class Google:
    """Gmail, Calendar (the `google` connector), on the mission starter's account.

    - gmail.messages.send: Z builds the RFC 2822 message from the reviewed fields (recipients, subject, text), so
      the agent cannot add headers or hidden recipients; it carries an `X-Z-Action` header. After a crash the
      account's latest SENT mails are read (headers only: the gmail.metadata scope) for it; only the account itself
      can put a mail in SENT.
    - calendar.events.insert: the event carries the private extended property `z_action` (private properties
      belong to this calendar's copy of the event: an invitation from someone else cannot carry it); after a crash
      the calendar is searched for it, and only an event this account created or organises counts.
    """

    @staticmethod
    def _calendar(p: dict[str, Any]) -> str:
        return quote(str(p.get("calendarId") or "primary"), safe="@")

    async def lookup(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction
    ) -> dict[str, Any] | None:
        if op.local_id == "gmail.messages.send":
            listed = await gw.send(
                connector,
                "GET",
                "/gmail/v1/users/me/messages",
                "write",
                params={"labelIds": "SENT", "maxResults": SENT_SCAN},
            )
            _fail(listed, "idempotency lookup")
            since_ms = (action.created_at.timestamp() - 60) * 1000  # tolerate clock skew with Google
            for item in (listed.body or {}).get("messages") or [] if isinstance(listed.body, dict) else []:
                got = await gw.send(
                    connector,
                    "GET",
                    f"/gmail/v1/users/me/messages/{quote(str(item.get('id', '')), safe='')}",
                    "write",
                    params={"format": "metadata", "metadataHeaders": MAIL_MARKER},
                )
                _fail(got, "idempotency lookup")
                mail = got.body if isinstance(got.body, dict) else {}
                headers = (mail.get("payload") or {}).get("headers") or []
                if any(
                    str(h.get("name", "")).lower() == MAIL_MARKER.lower() and h.get("value") == action.id
                    for h in headers
                    if isinstance(h, dict)
                ):
                    return {"id": mail.get("id"), "thread_id": mail.get("threadId"), "recovered": True}
                if int(mail.get("internalDate") or 0) < since_ms:
                    break  # newest first: older mails were sent before the proposal
            return None
        if op.local_id == "calendar.events.insert":
            inv = await gw.send(
                connector,
                "GET",
                f"/calendar/v3/calendars/{self._calendar(action.payload)}/events",
                "write",
                params={"privateExtendedProperty": f"z_action={action.id}", "showDeleted": "true", "maxResults": 5},
            )
            _fail(inv, "idempotency lookup")
            for event in (inv.body or {}).get("items") or [] if isinstance(inv.body, dict) else []:
                own = (event.get("creator") or {}).get("self") or (event.get("organizer") or {}).get("self")
                private = (event.get("extendedProperties") or {}).get("private") or {}
                if own and private.get("z_action") == action.id:
                    return {"id": event.get("id"), "url": event.get("htmlLink"), "recovered": True}
            return None
        return None

    @staticmethod
    def mime(p: dict[str, Any], action_id: str) -> bytes:
        message = EmailMessage(policy=policy.SMTP)
        try:
            message["To"] = ", ".join(p["to"])
            if p.get("cc"):
                message["Cc"] = ", ".join(p["cc"])
            message["Subject"] = p["subject"]
            if p.get("in_reply_to"):
                message["In-Reply-To"] = p["in_reply_to"]
                message["References"] = p["in_reply_to"]
            message[MAIL_MARKER] = action_id
            message.set_content(str(p.get("body") or ""))
        except (ValueError, TypeError) as exc:  # a header with a line break, a malformed address
            raise WriteError(f"the mail cannot be built: {exc}") from exc
        return message.as_bytes()

    async def apply(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction, files: dict[str, bytes]
    ) -> Applied:
        p = action.payload
        if op.local_id == "gmail.messages.send":
            mail = dict(p.get("body") or {})
            body: dict[str, Any] = {"raw": base64.urlsafe_b64encode(self.mime(mail, action.id)).decode()}
            if mail.get("thread_id"):
                body["threadId"] = mail["thread_id"]
            inv = await gw.send(connector, "POST", "/gmail/v1/users/me/messages/send", "write", json_body=body)
            _fail(inv, "send mail")
            sent = inv.body if isinstance(inv.body, dict) else {}
            return Applied(inv, {"id": sent.get("id"), "thread_id": sent.get("threadId")})
        if op.local_id == "calendar.events.insert":
            event = {**(p.get("body") or {}), "extendedProperties": {"private": {"z_action": action.id}}}
            params = {"sendUpdates": p["sendUpdates"]} if p.get("sendUpdates") else None
            inv = await gw.send(
                connector,
                "POST",
                f"/calendar/v3/calendars/{self._calendar(p)}/events",
                "write",
                json_body=event,
                params=params,
            )
            _fail(inv, "create event")
            created = inv.body if isinstance(inv.body, dict) else {}
            return Applied(inv, {"id": created.get("id"), "url": created.get("htmlLink")})
        return await Generic().apply(gw, connector, op, action, files)


class GoogleChat:
    """Google Chat as the mission starter. A post carries the action id as its `requestId`: Chat returns the
    existing message for a repeated request id instead of posting again, so a post after a crash is safe."""

    async def lookup(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction
    ) -> dict[str, Any] | None:
        return None  # messages.create is idempotent by its request id

    async def apply(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction, files: dict[str, bytes]
    ) -> Applied:
        if op.local_id != "messages.create":
            return await Generic().apply(gw, connector, op, action, files)
        p = action.payload
        params: dict[str, Any] = {"requestId": action.id}
        if p.get("messageReplyOption"):
            params["messageReplyOption"] = p["messageReplyOption"]
        inv = await gw.send(
            connector,
            "POST",
            f"/v1/spaces/{quote(str(p.get('space', '')), safe='')}/messages",
            "write",
            json_body=dict(p.get("body") or {}),
            params=params,
        )
        _fail(inv, "post")
        posted = inv.body if isinstance(inv.body, dict) else {}
        return Applied(inv, {"name": posted.get("name"), "thread": (posted.get("thread") or {}).get("name")})


class Microsoft365:
    """Microsoft Graph as the mission starter. An event is created with `transactionId = <action id>`: Graph does
    not create a second event for a repeated transaction id, so creating it again after a crash is safe. Mails and
    Teams posts have no such key: after a crash they end "outcome unknown" (never a blind second send)."""

    async def lookup(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction
    ) -> dict[str, Any] | None:
        return None

    async def apply(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction, files: dict[str, bytes]
    ) -> Applied:
        if op.local_id != "calendar.events.create":
            return await Generic().apply(gw, connector, op, action, files)
        event = {**(action.payload.get("body") or {}), "transactionId": action.id}
        inv = await gw.send(connector, "POST", "/me/events", "write", json_body=event)
        _fail(inv, "create event")
        created = inv.body if isinstance(inv.body, dict) else {}
        return Applied(inv, {"id": created.get("id"), "url": created.get("webLink")})


class Generic:
    async def lookup(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction
    ) -> dict[str, Any] | None:
        return None

    async def apply(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction, files: dict[str, bytes]
    ) -> Applied:
        inv = await gw.invoke(connector, op, dict(action.payload), "write")
        _fail(inv, op.id)
        body = inv.body if isinstance(inv.body, dict) else {"response": inv.body}
        return Applied(
            inv,
            {
                "status": inv.status,
                **{k: v for k, v in body.items() if k in {"id", "key", "iid", "web_url", "url", "self"}},
            },
        )


def writer_for(connector: Connector, op: Operation) -> Writer:
    if connector.kind in {"rest", "openapi"} and op.id.startswith("jira."):
        return JiraCloud()
    if connector.kind in {"rest", "openapi"} and op.id.startswith("gitlab."):
        return GitLab()
    if connector.kind in {"rest", "openapi"} and op.id.startswith("mattermost."):
        return Mattermost()
    if connector.kind in {"rest", "openapi"} and op.id.startswith("slack."):
        return Slack()
    if connector.kind in {"rest", "openapi"} and op.id.startswith("m365."):
        return Microsoft365()
    if connector.kind in {"rest", "openapi"} and op.id.startswith("google."):
        return Google()
    if connector.kind in {"rest", "openapi"} and op.id.startswith("gchat."):
        return GoogleChat()
    return Generic()


def idempotent(op: Operation) -> bool:
    return (
        op.source_ref.split(" ", 1)[0].upper() in {"PUT", "DELETE"}
        or op.local_id in {"issues.update", "issues.transition"}
        # a repeated Chat request id returns the first post; a repeated Graph transactionId the first event
        or op.id in {"gchat.messages.create", "m365.calendar.events.create"}
    )
