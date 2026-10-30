"""Fake Confluence: REST subset under `/confluence/rest/api` and an MCP server at `/confluence/mcp`. Bearer auth."""

import copy
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from .._mcp import McpTool, TokenKind
from .errors import BadRequest, NotFound
from .query import matches
from .state import Store


class Confluence:
    def __init__(self, store: Store) -> None:
        self.store = store

    @property
    def data(self) -> dict[str, Any]:
        result: dict[str, Any] = self.store.data["confluence"]
        return result

    def _render(self, page: dict[str, Any], body: bool = True) -> dict[str, Any]:
        out = copy.deepcopy(page)
        if not body:
            out.pop("body", None)
        out["_links"] = {
            "webui": f"/spaces/{page['space']['key']}/pages/{page['id']}",
            "self": f"{self.store.base_url}/confluence/rest/api/content/{page['id']}",
        }
        return out

    def search(self, cql: str, limit: int = 25, start: int = 0) -> dict[str, Any]:
        if cql and not re.search(r"[=~]", cql):
            cql = f'text ~ "{cql}"'  # plain text query

        def accessor(page: dict[str, Any]) -> Any:
            text = re.sub(r"<[^>]+>", " ", page["body"]["storage"]["value"])
            table = {
                "space": [page["space"]["key"]],
                "space.key": [page["space"]["key"]],
                "type": [page["type"]],
                "title": [page["title"]],
                "text": [page["title"], text],
                "id": [page["id"]],
            }
            return table.get

        try:
            hits = [p for p in self.data["pages"] if matches(cql, accessor(p))]
        except ValueError as exc:
            raise BadRequest(str(exc), {"statusCode": 400, "message": str(exc)}) from exc
        page = hits[start : start + limit]
        return {
            "results": [self._render(p, body=False) for p in page],
            "start": start,
            "limit": limit,
            "size": len(page),
            "totalSize": len(hits),
        }

    def get(self, page_id: str) -> dict[str, Any]:
        for page in self.data["pages"]:
            if page["id"] == page_id:
                return self._render(page)
        raise NotFound(f"No content found with id: {page_id}", {"statusCode": 404, "message": "not found"})

    def create(
        self, space_key: str, title: str, content: str, parent_id: str | None, kind: TokenKind
    ) -> dict[str, Any]:
        if space_key not in {s["key"] for s in self.data["spaces"]}:
            raise BadRequest(f"No space with key : {space_key}", {"statusCode": 400, "message": "unknown space"})
        with self.store.lock:
            pid = str(max((int(p["id"]) for p in self.data["pages"]), default=10000) + 1)
            page = {
                "id": pid,
                "type": "page",
                "status": "current",
                "title": title,
                "space": {"key": space_key},
                "version": {"number": 1},
                "body": {"storage": {"value": content, "representation": "storage"}},
                "ancestors": [{"id": parent_id}] if parent_id else [],
            }
            self.data["pages"].append(page)
        self.store.record(
            "confluence", "confluence.pages.create", kind, {"id": pid, "space_key": space_key, "title": title}
        )
        return self._render(page)


def router(store: Store, conf: Confluence) -> APIRouter:
    r = APIRouter(prefix="/confluence/rest/api")
    read = Depends(store.dep("confluence", "bearer", "read"))
    write = store.dep("confluence", "bearer", "write")

    @r.get("/content/search", dependencies=[read])
    @r.get("/search", dependencies=[read])
    def search(cql: str = "", limit: int = 25, start: int = 0) -> dict[str, Any]:
        return conf.search(cql, min(limit, 100), start)

    @r.get("/content/{page_id}", dependencies=[read])
    def get(page_id: str) -> dict[str, Any]:
        return conf.get(page_id)

    @r.post("/content")
    async def create(request: Request, kind: Annotated[TokenKind, Depends(write)]) -> dict[str, Any]:
        b = await request.json()
        ancestors = b.get("ancestors") or [{}]
        return conf.create(
            b.get("space", {}).get("key", ""),
            b.get("title", ""),
            b.get("body", {}).get("storage", {}).get("value", ""),
            ancestors[0].get("id"),
            kind,
        )

    return r


def mcp_tools(conf: Confluence) -> list[McpTool]:
    def obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": required, "additionalProperties": False}

    return [
        McpTool(
            "confluence_search",
            "Search Confluence pages (CQL or plain text).",
            obj(
                {
                    "query": {"type": "string", "description": 'CQL (space = SEC AND text ~ "baseline") or text'},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
                ["query"],
            ),
            lambda a, k: conf.search(a["query"], a.get("limit", 10)),
        ),
        McpTool(
            "confluence_get_page",
            "Get a Confluence page with its storage-format body.",
            obj({"page_id": {"type": "string"}}, ["page_id"]),
            lambda a, k: conf.get(a["page_id"]),
        ),
        McpTool(
            "confluence_create_page",
            "Create a Confluence page.",
            obj(
                {
                    "space_key": {"type": "string"},
                    "title": {"type": "string", "minLength": 1},
                    "content": {"type": "string"},
                    "parent_id": {"type": "string"},
                },
                ["space_key", "title", "content"],
            ),
            lambda a, k: conf.create(a["space_key"], a["title"], a["content"], a.get("parent_id"), k),
            access="write",
        ),
    ]
