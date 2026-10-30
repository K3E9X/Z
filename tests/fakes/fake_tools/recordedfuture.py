"""Fake Recorded Future Connect API implementing packs/vuln/connectors/recordedfuture.openapi.json.

Like the real API (shapes from Recorded Future's SDK psengine 2.14.0): the token comes in `X-RFToken`, a lookup
answers `{"data": {...}}` with only the requested `fields`, and an unknown entity is a 404. Every lookup is
recorded as a read so tests can check which indicators left Z.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request

from .state import Store

ENTITIES: dict[tuple[str, str], dict[str, Any]] = {
    ("vulnerability", "CVE-2021-44228"): {
        "entity": {"id": "OC4yiT", "name": "CVE-2021-44228", "type": "CyberVulnerability"},
        "risk": {
            "score": 99,
            "criticality": 4,
            "criticalityLabel": "Very Critical",
            "riskString": "12/22",
            "rules": 12,
            "riskSummary": "12 of 22 Risk Rules currently observed.",
            "evidenceDetails": [
                {"rule": "Exploited in the Wild by Recently Active Malware", "criticality": 4},
                {"rule": "Linked to Ransomware", "criticality": 3},
            ],
        },
        "timestamps": {"firstSeen": "2021-12-09T00:00:00.000Z", "lastSeen": "2026-10-28T00:00:00.000Z"},
        "lifecycleStage": "Exploited",
        "cvssv3": {"baseScore": 10.0, "baseSeverity": "CRITICAL"},
    },
    ("ip", "185.220.101.1"): {
        "entity": {"id": "ip:185.220.101.1", "name": "185.220.101.1", "type": "IpAddress"},
        "risk": {"score": 85, "criticalityLabel": "Malicious", "riskString": "5/64", "rules": 5, "criticality": 3},
        "timestamps": {"firstSeen": "2020-01-01T00:00:00.000Z", "lastSeen": "2026-10-28T00:00:00.000Z"},
    },
}


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/recordedfuture/v2")

    def check(request: Request) -> None:
        if store.kind("recordedfuture", request.headers.get("x-rftoken")) is None:
            store.deny("recordedfuture", request.url.path, "missing or unknown X-RFToken")
            raise HTTPException(401, "Unauthorized")

    @r.get("/{entity_type}/riskrules")
    def risk_rules(entity_type: str, request: Request) -> dict[str, Any]:
        check(request)
        return {"data": {"results": [{"name": "linkedToRansomware", "criticality": 3}]}}

    @r.get("/{entity_type}/{value:path}")
    def lookup(entity_type: str, value: str, request: Request, fields: str = "entity,risk,timestamps") -> Any:
        check(request)
        name = unquote(value)
        store.record("recordedfuture", f"recordedfuture.{entity_type}.get", "read", {"entity": name})
        found = ENTITIES.get((entity_type, name))
        if found is None:
            raise HTTPException(404, "Not Found")
        wanted = set(fields.split(","))
        return {"data": {k: v for k, v in found.items() if k in wanted}, "metadata": {"entries": []}}

    return r
