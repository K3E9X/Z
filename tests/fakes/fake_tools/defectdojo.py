"""Fake DefectDojo API v2 subset (`/defectdojo/api/v2/...`), auth `Authorization: Token <token>`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .state import Store

PAGE = 25


def public(obj: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in obj.items() if not k.startswith("_")}


def _page(request: Request, items: list[dict[str, Any]], limit: int, offset: int) -> dict[str, Any]:
    def link(off: int) -> str:
        return str(request.url.include_query_params(limit=limit, offset=off))

    nxt = link(offset + limit) if offset + limit < len(items) else None
    prev = link(max(0, offset - limit)) if offset > 0 else None
    return {
        "count": len(items),
        "next": nxt,
        "previous": prev,
        "results": [public(x) for x in items[offset : offset + limit]],
        "prefetch": {},
    }


def _bool(value: str | None) -> bool | None:
    return None if value is None else value.lower() in ("true", "1", "yes")


def openapi_document(server_url: str) -> dict[str, Any]:
    """OpenAPI 3.0 document for exactly the served endpoints (paths follow DefectDojo's)."""
    obj = {"type": "object", "additionalProperties": True}

    def paginated(name: str) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["count", "results"],
            "properties": {
                "count": {"type": "integer"},
                "next": {"type": "string", "nullable": True, "format": "uri"},
                "previous": {"type": "string", "nullable": True, "format": "uri"},
                "results": {"type": "array", "items": {"$ref": f"#/components/schemas/{name}"}},
            },
        }

    def ok(schema: dict[str, Any]) -> dict[str, Any]:
        return {
            "200": {"description": "OK", "content": {"application/json": {"schema": schema}}},
            "401": {"description": "Unauthorized"},
            "404": {"description": "Not found"},
        }

    def q(name: str, typ: str, desc: str, enum: list[str] | None = None) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": typ}
        if enum:
            schema["enum"] = enum
        return {"name": name, "in": "query", "required": False, "description": desc, "schema": schema}

    page = [
        q("limit", "integer", "Number of results to return per page."),
        q("offset", "integer", "The initial index from which to return the results."),
    ]
    pid = {
        "name": "id",
        "in": "path",
        "required": True,
        "schema": {"type": "integer"},
        "description": "A unique integer value identifying this object.",
    }
    sec = [{"tokenAuth": []}]
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Defect Dojo API v2 (fake subset)",
            "version": "2.39.0",
            "description": "Synthetic subset served by tests/fakes/fake_tools.",
        },
        "servers": [{"url": server_url}],
        "security": sec,
        "paths": {
            "/api/v2/findings/": {
                "get": {
                    "operationId": "findings_list",
                    "tags": ["findings"],
                    "summary": "List findings",
                    "parameters": [
                        q("product_name", "string", "Exact product name."),
                        q(
                            "severity",
                            "string",
                            "Severity (comma-separated allowed).",
                            ["Critical", "High", "Medium", "Low", "Info"],
                        ),
                        q("active", "boolean", "Only active (true) or inactive (false) findings."),
                        q("vulnerability_id", "string", "CVE or other vulnerability id."),
                        *page,
                    ],
                    "responses": ok({"$ref": "#/components/schemas/PaginatedFindingList"}),
                }
            },
            "/api/v2/findings/{id}/": {
                "get": {
                    "operationId": "findings_retrieve",
                    "tags": ["findings"],
                    "summary": "Get a finding",
                    "parameters": [pid],
                    "responses": ok({"$ref": "#/components/schemas/Finding"}),
                }
            },
            "/api/v2/products/": {
                "get": {
                    "operationId": "products_list",
                    "tags": ["products"],
                    "summary": "List products",
                    "parameters": [q("name", "string", "Exact product name."), *page],
                    "responses": ok({"$ref": "#/components/schemas/PaginatedProductList"}),
                }
            },
            "/api/v2/products/{id}/": {
                "get": {
                    "operationId": "products_retrieve",
                    "tags": ["products"],
                    "summary": "Get a product",
                    "parameters": [pid],
                    "responses": ok({"$ref": "#/components/schemas/Product"}),
                }
            },
            "/api/v2/engagements/{id}/": {
                "get": {
                    "operationId": "engagements_retrieve",
                    "tags": ["engagements"],
                    "summary": "Get an engagement",
                    "parameters": [pid],
                    "responses": ok({"$ref": "#/components/schemas/Engagement"}),
                }
            },
        },
        "components": {
            "securitySchemes": {
                "tokenAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "Authorization",
                    "description": "Token-based authentication: `Token <key>`",
                }
            },
            "schemas": {
                "Finding": {
                    **obj,
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "severity": {"type": "string"},
                        "active": {"type": "boolean"},
                        "cve": {"type": "string", "nullable": True},
                        "cvssv3_score": {"type": "number"},
                        "component_name": {"type": "string"},
                        "component_version": {"type": "string"},
                        "service": {"type": "string"},
                        "description": {"type": "string"},
                        "mitigation": {"type": "string"},
                    },
                },
                "Product": {**obj, "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}},
                "Engagement": {
                    **obj,
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "product": {"type": "integer"},
                    },
                },
                "PaginatedFindingList": paginated("Finding"),
                "PaginatedProductList": paginated("Product"),
            },
        },
    }


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/defectdojo")
    read = Depends(store.dep("defectdojo", "token", "read"))

    def data() -> dict[str, Any]:
        result: dict[str, Any] = store.data["defectdojo"]
        return result

    def get(kind: str, oid: int) -> dict[str, Any]:
        for item in data()[kind]:
            if item["id"] == oid:
                return public(item)
        raise HTTPException(404, detail="No Finding matches the given query." if kind == "findings" else "Not found.")

    @r.get("/api/v2/oa3/schema/")
    def schema(request: Request, format: str = "json") -> dict[str, Any]:
        return openapi_document(str(request.base_url).rstrip("/") + "/defectdojo")

    @r.get("/api/v2/findings/", dependencies=[read])
    def findings(
        request: Request,
        product_name: str | None = None,
        severity: str | None = None,
        active: str | None = None,
        vulnerability_id: str | None = None,
        limit: int = PAGE,
        offset: int = 0,
    ) -> dict[str, Any]:
        products = {p["name"]: p["id"] for p in data()["products"]}
        items = list(data()["findings"])
        if product_name is not None:
            items = [f for f in items if f["_product"] == products.get(product_name)]
        if severity:
            wanted = {s.strip().lower() for s in severity.split(",")}
            items = [f for f in items if f["severity"].lower() in wanted]
        if (flag := _bool(active)) is not None:
            items = [f for f in items if f["active"] is flag]
        if vulnerability_id:
            items = [f for f in items if vulnerability_id in [v["vulnerability_id"] for v in f["vulnerability_ids"]]]
        return _page(request, items, max(1, limit), max(0, offset))

    @r.get("/api/v2/findings/{fid}/", dependencies=[read])
    def finding(fid: int) -> dict[str, Any]:
        return get("findings", fid)

    @r.get("/api/v2/products/", dependencies=[read])
    def products(request: Request, name: str | None = None, limit: int = PAGE, offset: int = 0) -> dict[str, Any]:
        items = [p for p in data()["products"] if name is None or p["name"] == name]
        return _page(request, items, max(1, limit), max(0, offset))

    @r.get("/api/v2/products/{pid}/", dependencies=[read])
    def product(pid: int) -> dict[str, Any]:
        return get("products", pid)

    @r.get("/api/v2/engagements/{eid}/", dependencies=[read])
    def engagement(eid: int) -> dict[str, Any]:
        return get("engagements", eid)

    return r
