"""Approved memory facts and similarity search (docs/08 §7). Facts never hold C3 content."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from z_server.db.models import MemoryFact
from z_server.missions.service import utcnow

EMBED_ALIAS = "z-embed"


class MemoryUnavailable(RuntimeError):
    pass


def scope_filter(scopes: list[str], team: str | None, agent: str) -> Any:
    clauses = []
    if "org" in scopes:
        clauses.append(MemoryFact.scope == "org")
    if "team" in scopes and team:
        clauses.append((MemoryFact.scope == "team") & (MemoryFact.scope_ref == team))
    if "agent" in scopes:
        clauses.append((MemoryFact.scope == "agent") & (MemoryFact.scope_ref == agent))
    return or_(*clauses) if clauses else MemoryFact.id.is_(None)


async def embed(http: httpx.AsyncClient, litellm_url: str, api_key: str, text: str) -> list[float]:
    try:
        resp = await http.post(
            f"{litellm_url}/v1/embeddings",
            json={"model": EMBED_ALIAS, "input": text},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise MemoryUnavailable(f"embedding call failed: {exc}") from exc
    if resp.status_code != 200:
        raise MemoryUnavailable(f"embedding call refused ({resp.status_code})")
    return [float(x) for x in resp.json()["data"][0]["embedding"]]


async def search(
    db: AsyncSession,
    http: httpx.AsyncClient,
    litellm_url: str,
    api_key: str,
    *,
    query: str,
    scopes: list[str],
    team: str | None,
    agent: str,
    top_k: int,
) -> list[MemoryFact]:
    where = (
        MemoryFact.active.is_(True),
        or_(MemoryFact.expires_at.is_(None), MemoryFact.expires_at > utcnow()),
        scope_filter(scopes, team, agent),
    )
    count = (await db.execute(select(func.count()).select_from(MemoryFact).where(*where))).scalar_one()
    if not count or top_k <= 0:
        return []
    vector = await embed(http, litellm_url, api_key, query)
    stmt = select(MemoryFact).where(*where).order_by(MemoryFact.embedding.cosine_distance(vector)).limit(top_k)
    return list((await db.execute(stmt)).scalars())
