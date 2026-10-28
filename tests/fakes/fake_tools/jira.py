"""Fake Jira: REST API v2 subset under `/jira/rest/api/2` and an MCP server at `/jira/mcp`. Bearer auth."""

import copy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from .._mcp import McpTool, TokenKind
from .errors import BadRequest, NotFound
from .query import matches
from .state import Store, now_iso

CATEGORY_NAMES = {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}
STATUS_CATEGORY = {"open": "new", "to do": "new", "in progress": "indeterminate", "done": "done", "closed": "done"}
KEY = {"type": "string", "pattern": r"^[A-Z][A-Z0-9]+-\d+$", "description": "Issue key, e.g. SECOPS-4791"}


class Jira:
    def __init__(self, store: Store) -> None:
        self.store = store

    @property
    def data(self) -> dict[str, Any]:
        result: dict[str, Any] = self.store.data["jira"]
        return result

    def _link(self, issue: dict[str, Any]) -> str:
        return f"{self.store.base_url}/jira/rest/api/2/issue/{issue['id']}"

    def _find(self, key: str) -> dict[str, Any]:
        for issue in self.data["issues"]:
            if key in (issue["key"], issue["id"]):
                return issue
        raise NotFound(
            "Issue does not exist or you do not have permission to see it.",
            {"errorMessages": ["Issue does not exist or you do not have permission to see it."], "errors": {}},
        )

    def _render(self, issue: dict[str, Any]) -> dict[str, Any]:
        return {
            "expand": "renderedFields,names,schema",
            "id": issue["id"],
            "key": issue["key"],
            "self": self._link(issue),
            "fields": copy.deepcopy(issue["fields"]),
        }

    @staticmethod
    def _accessor(issue: dict[str, Any]) -> Any:
        f = issue["fields"]

        def get(field: str) -> list[str] | None:
            comments = [c["body"] for c in f["comment"]["comments"]]
            category = f["status"]["statusCategory"]["key"]
            table: dict[str, list[str]] = {
                "project": [f["project"]["key"], f["project"]["name"]],
                "key": [issue["key"]],
                "issuekey": [issue["key"]],
                "id": [issue["id"]],
                "status": [f["status"]["name"]],
                "statuscategory": [category, CATEGORY_NAMES.get(category, category)],
                "labels": list(f["labels"]),
                "priority": [f["priority"]["name"]] if f.get("priority") else [],
                "issuetype": [f["issuetype"]["name"]],
                "type": [f["issuetype"]["name"]],
                "summary": [f["summary"]],
                "description": [f.get("description") or ""],
                "comment": comments,
                "text": [f["summary"], f.get("description") or "", *comments],
                "resolution": [f["resolution"]["name"]] if f.get("resolution") else ["unresolved"],
                "assignee": [f["assignee"]["displayName"]] if f.get("assignee") else [],
            }
            return table.get(field)

        return get

    # ---- operations ----
    def search(self, jql: str, start_at: int = 0, max_results: int = 50) -> dict[str, Any]:
        try:
            hits = [i for i in self.data["issues"] if matches(jql, self._accessor(i))]
        except ValueError as exc:
            raise BadRequest(str(exc), {"errorMessages": [str(exc)], "warningMessages": []}) from exc
        hits.sort(key=lambda i: int(i["id"]), reverse=True)
        page = hits[start_at : start_at + max_results]
        return {
            "expand": "names,schema",
            "startAt": start_at,
            "maxResults": max_results,
            "total": len(hits),
            "issues": [self._render(i) for i in page],
        }

    def get(self, key: str) -> dict[str, Any]:
        return self._render(self._find(key))

    def create(self, fields: dict[str, Any], kind: TokenKind) -> dict[str, Any]:
        errors: dict[str, str] = {}
        project_key = (fields.get("project") or {}).get("key")
        projects = {p["key"]: p for p in self.data["projects"]}
        if project_key not in projects:
            errors["project"] = "valid project is required"
        if not fields.get("summary"):
            errors["summary"] = "You must specify a summary of the issue."
        if not (fields.get("issuetype") or {}).get("name"):
            errors["issuetype"] = "issue type is required"
        if errors:
            raise BadRequest("invalid issue fields", {"errorMessages": [], "errors": errors})
        with self.store.lock:
            numbers = [
                int(i["key"].split("-")[1]) for i in self.data["issues"] if i["key"].startswith(f"{project_key}-")
            ]
            num = max(numbers, default=0) + 1
            ids = [int(i["id"]) for i in self.data["issues"]]
            issue = {
                "id": str(max(ids, default=20000) + 1),
                "key": f"{project_key}-{num}",
                "fields": {
                    "summary": fields["summary"],
                    "description": fields.get("description", ""),
                    "status": {"name": "Open", "statusCategory": {"key": "new"}},
                    "resolution": None,
                    "issuetype": {"name": fields["issuetype"]["name"]},
                    "priority": {"name": (fields.get("priority") or {}).get("name", "Medium")},
                    "labels": list(fields.get("labels", [])),
                    "project": {"key": project_key, "name": projects[project_key]["name"]},
                    "reporter": {"displayName": "Z Service", "emailAddress": "z-bot@example.test"},
                    "assignee": None,
                    "created": now_iso(),
                    "updated": now_iso(),
                    "comment": {"comments": [], "total": 0},
                    **{k: v for k, v in fields.items() if k.startswith("customfield_")},
                },
            }
            self.data["issues"].append(issue)
        self.store.record("jira", "jira.issues.create", kind, {"key": issue["key"], "fields": fields})
        return {"id": issue["id"], "key": issue["key"], "self": self._link(issue)}

    def update(self, key: str, fields: dict[str, Any], update: dict[str, Any] | None, kind: TokenKind) -> None:
        with self.store.lock:
            issue = self._find(key)
            f = issue["fields"]
            for name, value in fields.items():
                if name in ("priority", "issuetype") and isinstance(value, str):
                    value = {"name": value}
                if name == "status":
                    status = value["name"] if isinstance(value, dict) else str(value)
                    value = {"name": status, "statusCategory": {"key": STATUS_CATEGORY.get(status.lower(), "new")}}
                f[name] = value
            for op in (update or {}).get("labels", []):
                if "add" in op and op["add"] not in f["labels"]:
                    f["labels"].append(op["add"])
                if "remove" in op and op["remove"] in f["labels"]:
                    f["labels"].remove(op["remove"])
            f["updated"] = now_iso()
        self.store.record(
            "jira", "jira.issues.update", kind, {"key": issue["key"], "fields": fields, "update": update or {}}
        )

    def comment(self, key: str, body: str, kind: TokenKind) -> dict[str, Any]:
        if not body:
            raise BadRequest(
                "Comment body can not be empty!",
                {"errorMessages": [], "errors": {"comment": "Comment body can not be empty!"}},
            )
        with self.store.lock:
            issue = self._find(key)
            comments = issue["fields"]["comment"]
            comment = {
                "id": str(30000 + self.store.next_id()),
                "body": body,
                "created": now_iso(),
                "author": {"displayName": "Z Service", "emailAddress": "z-bot@example.test"},
            }
            comments["comments"].append(comment)
            comments["total"] = len(comments["comments"])
        self.store.record(
            "jira", "jira.comments.create", kind, {"key": issue["key"], "comment_id": comment["id"], "body": body}
        )
        return {**comment, "self": f"{self._link(issue)}/comment/{comment['id']}"}

    def delete(self, key: str, kind: TokenKind) -> None:
        with self.store.lock:
            issue = self._find(key)
            self.data["issues"].remove(issue)
        self.store.record("jira", "jira.issues.delete", kind, {"key": issue["key"]})


def router(store: Store, jira: Jira) -> APIRouter:
    r = APIRouter(prefix="/jira/rest/api/2")
    read = Depends(store.dep("jira", "bearer", "read"))
    write = store.dep("jira", "bearer", "write")

    @r.get("/search", dependencies=[read])
    def search(jql: str = "", startAt: int = 0, maxResults: int = 50) -> dict[str, Any]:
        return jira.search(jql, startAt, min(maxResults, 100))

    @r.post("/search", dependencies=[read])
    async def search_post(request: Request) -> dict[str, Any]:
        body = await request.json()
        return jira.search(body.get("jql", ""), body.get("startAt", 0), min(body.get("maxResults", 50), 100))

    @r.get("/issue/{key}", dependencies=[read])
    def get_issue(key: str) -> dict[str, Any]:
        return jira.get(key)

    @r.post("/issue", status_code=201)
    async def create(request: Request, kind: Annotated[TokenKind, Depends(write)]) -> dict[str, Any]:
        body = await request.json()
        return jira.create(body.get("fields") or {}, kind)

    @r.put("/issue/{key}", status_code=204)
    async def update(key: str, request: Request, kind: Annotated[TokenKind, Depends(write)]) -> Response:
        body = await request.json()
        jira.update(key, body.get("fields") or {}, body.get("update"), kind)
        return Response(status_code=204)

    @r.post("/issue/{key}/comment", status_code=201)
    async def comment(key: str, request: Request, kind: Annotated[TokenKind, Depends(write)]) -> dict[str, Any]:
        body = await request.json()
        return jira.comment(key, body.get("body", ""), kind)

    @r.get("/issue/{key}/comment", dependencies=[read])
    def comments(key: str) -> dict[str, Any]:
        c = jira.get(key)["fields"]["comment"]
        return {"startAt": 0, "maxResults": c["total"], "total": c["total"], "comments": c["comments"]}

    @r.delete("/issue/{key}", status_code=204)
    def delete(key: str, kind: Annotated[TokenKind, Depends(write)]) -> Response:
        jira.delete(key, kind)
        return Response(status_code=204)

    @r.get("/project", dependencies=[read])
    def projects() -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = jira.data["projects"]
        return result

    return r


def mcp_tools(jira: Jira) -> list[McpTool]:
    def obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": required, "additionalProperties": False}

    def create(a: dict[str, Any], kind: TokenKind) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "project": {"key": a["project_key"]},
            "summary": a["summary"],
            "issuetype": {"name": a.get("issue_type", "Task")},
            "description": a.get("description", ""),
            "labels": a.get("labels", []),
            **(a.get("additional_fields") or {}),
        }
        if a.get("priority"):
            fields["priority"] = {"name": a["priority"]}
        return jira.create(fields, kind)

    def update(a: dict[str, Any], kind: TokenKind) -> dict[str, Any]:
        jira.update(a["issue_key"], a["fields"], None, kind)
        return jira.get(a["issue_key"])

    return [
        McpTool(
            "jira_search",
            "Search Jira issues with JQL.",
            obj(
                {
                    "jql": {"type": "string", "description": "JQL, e.g. project = SECOPS AND statusCategory != Done"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "start_at": {"type": "integer", "minimum": 0, "default": 0},
                    "fields": {"type": "string", "description": "Comma-separated fields (ignored by the fake)"},
                },
                ["jql"],
            ),
            lambda a, k: jira.search(a["jql"], a.get("start_at", 0), a.get("limit", 10)),
        ),
        McpTool(
            "jira_get_issue",
            "Get one Jira issue by key.",
            obj({"issue_key": KEY}, ["issue_key"]),
            lambda a, k: jira.get(a["issue_key"]),
        ),
        McpTool(
            "jira_create_issue",
            "Create a Jira issue.",
            obj(
                {
                    "project_key": {"type": "string", "pattern": r"^[A-Z][A-Z0-9]+$"},
                    "summary": {"type": "string", "minLength": 1},
                    "issue_type": {"type": "string", "default": "Task"},
                    "description": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "priority": {"type": "string", "enum": ["Highest", "High", "Medium", "Low", "Lowest"]},
                    "additional_fields": {"type": "object"},
                },
                ["project_key", "summary"],
            ),
            create,
            access="write",
        ),
        McpTool(
            "jira_update_issue",
            "Update fields of a Jira issue (summary, description, labels, priority, status).",
            obj({"issue_key": KEY, "fields": {"type": "object"}}, ["issue_key", "fields"]),
            update,
            access="write",
        ),
        McpTool(
            "jira_add_comment",
            "Add a comment to a Jira issue.",
            obj({"issue_key": KEY, "comment": {"type": "string", "minLength": 1}}, ["issue_key", "comment"]),
            lambda a, k: jira.comment(a["issue_key"], a["comment"], k),
            access="write",
        ),
        McpTool(
            "jira_delete_issue",
            "Delete a Jira issue.",
            obj({"issue_key": KEY}, ["issue_key"]),
            lambda a, k: (jira.delete(a["issue_key"], k), {"deleted": a["issue_key"]})[1],
            access="destructive",
        ),
    ]
