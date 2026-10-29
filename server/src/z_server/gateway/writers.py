"""Connector-specific write adapters for the action executor (docs/05 §2 "action executor").

An adapter turns the reviewed payload (models/actions.py built-ins) into the tool's API calls, and can look up
whether an earlier attempt already applied the action (idempotency after a crash). Operations without an
adapter use the generic path: the operation's own request with the payload as arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from z_server.db.models import Connector, Operation, ProposedAction
from z_server.gateway import adf
from z_server.gateway import http as http_invoker
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

    async def apply(self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction) -> Applied: ...


def action_label(action: ProposedAction) -> str:
    return f"z-{action.id.lower()}"


def _fail(inv: Invocation, what: str) -> None:
    if not inv.ok:
        detail = inv.body if isinstance(inv.body, dict | str) else None
        raise WriteError(f"{what}: {inv.error}" + (f" {str(detail)[:500]}" if detail else ""))


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

    async def apply(self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction) -> Applied:
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
        return await Generic().apply(gw, connector, op, action)


class Generic:
    async def lookup(
        self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction
    ) -> dict[str, Any] | None:
        return None

    async def apply(self, gw: Gateway, connector: Connector, op: Operation, action: ProposedAction) -> Applied:
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
    return Generic()


def idempotent(op: Operation) -> bool:
    return op.source_ref.split(" ", 1)[0].upper() in {"PUT", "DELETE"} or op.local_id in {
        "issues.update",
        "issues.transition",
    }
