"""Inference monitor (docs/06 §2, Inference screen): the LiteLLM gateway, the aliases it serves and, when a
Prometheus URL is configured, vLLM and DCGM metrics. Prometheus label `model_name` is matched against the
alias or against the upstream model id LiteLLM routes the alias to (`/model/info`). Tool-call health per alias comes
from the synthetic probe (probes.py)."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.config import substitute

from z_server.db.models import Provider, UsageRecord
from z_server.state import AppState

CACHE_S = 15.0
_cache: dict[str, tuple[float, Any]] = {}


def prometheus_url(config: dict[str, Any]) -> str:
    raw = ((config.get("inference") or {}).get("prometheus") or {}).get("url") or ""
    value = substitute(raw, os.environ) if isinstance(raw, str) else ""
    return value.rstrip("/") if isinstance(value, str) and value.startswith("http") else ""


async def providers(db: AsyncSession, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Providers from config/z.yaml, overridden and extended by the ones an admin added (database)."""
    out = {
        p["name"]: {**p, "id": f"cfg_{p['name'].lower().replace(' ', '_')}", "origin": "config"}
        for p in config.get("providers", [])
    }
    for row in (await db.execute(select(Provider))).scalars():
        out[row.name] = {
            "id": row.id,
            "name": row.name,
            "kind": row.kind,
            "aliases": list(row.aliases),
            "data_classes": list(row.data_classes),
            "enabled": row.enabled,
            "origin": "admin",
        }
    if not out:
        aliases = (config.get("inference") or {}).get("aliases", [])
        out["internal"] = {
            "id": "cfg_internal",
            "name": "internal",
            "kind": "internal",
            "aliases": aliases,
            "data_classes": ["C0", "C1", "C2", "C3"],
            "enabled": True,
            "origin": "config",
        }
    return list(out.values())


async def routing_config(db: AsyncSession, config: dict[str, Any]) -> dict[str, Any]:
    """The config with the effective provider list and routing policy, for the key allowlists."""
    from z_server.llm import policy

    return {**config, "providers": await providers(db, config), "routing_policy": await policy.effective(db, config)}


async def _gateway(state: AppState) -> tuple[bool, int | None, str | None, dict[str, str], set[str]]:
    base = state.settings.litellm_url.rstrip("/")
    started = time.monotonic()
    headers = (
        {"Authorization": f"Bearer {state.settings.litellm_master_key}"} if state.settings.litellm_master_key else {}
    )
    try:
        live = await state.http.get(f"{base}/health/liveliness", timeout=5)
        latency = int((time.monotonic() - started) * 1000)
        if live.status_code != 200:
            return False, latency, f"LiteLLM answered {live.status_code}", {}, set()
        upstream: dict[str, str] = {}
        listed: set[str] = set()
        info = await state.http.get(f"{base}/model/info", headers=headers, timeout=5)
        if info.status_code == 200:
            for entry in info.json().get("data", []):
                alias = str(entry.get("model_name", ""))
                listed.add(alias)
                model = str((entry.get("litellm_params") or {}).get("model", ""))
                upstream[alias] = model.split("/", 1)[-1]
        else:
            models = await state.http.get(f"{base}/v1/models", headers=headers, timeout=5)
            if models.status_code == 200:
                listed = {str(m.get("id")) for m in models.json().get("data", [])}
        return True, latency, None, upstream, listed
    except httpx.HTTPError as exc:
        return False, None, f"LiteLLM unreachable: {type(exc).__name__}", {}, set()


async def _query(state: AppState, url: str, query: str) -> list[tuple[dict[str, str], float]]:
    try:
        resp = await state.http.get(f"{url}/api/v1/query", params={"query": query}, timeout=5)
        body = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    if resp.status_code != 200 or body.get("status") != "success":
        return []
    out = []
    for sample in (body.get("data") or {}).get("result", []):
        try:
            out.append((dict(sample.get("metric") or {}), float(sample["value"][1])))
        except (KeyError, ValueError, TypeError, IndexError):
            continue
    return out


async def health(db: AsyncSession, state: AppState) -> dict[str, Any]:
    key = f"health:{id(state)}"
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        return dict(cached[1])
    config = state.config
    ok, latency, error, upstream, listed = await _gateway(state)
    prom = prometheus_url(config)
    queries: dict[str, str] = ((config.get("inference") or {}).get("prometheus") or {}).get("queries") or {}
    metrics: dict[str, list[tuple[dict[str, str], float]]] = {}
    if prom:
        for name, query in queries.items():
            metrics[name] = await _query(state, prom, query)

    def per_model(name: str, alias: str) -> float | None:
        targets = {alias, upstream.get(alias, "")} - {""}
        for labels, value in metrics.get(name, []):
            if labels.get("model_name") in targets or labels.get("model") in targets:
                return value
        return None

    def scalar(name: str) -> float | None:
        rows = metrics.get(name) or []
        return (
            sum(v for _, v in rows)
            if rows and all(not labels for labels, _ in rows)
            else (rows[0][1] if len(rows) == 1 else None)
        )

    from z_server import probes  # imports this module

    probed = probes.summary(await probes.load(db))  # synthetic tool-call probe per alias (ADR 0034)
    models = []
    for provider in await providers(db, config):
        if not provider.get("enabled"):
            continue
        for alias in provider.get("aliases", []):
            is_listed = alias in listed
            waiting = per_model("requests_waiting", alias)
            status = (
                "unknown"
                if not ok
                else ("down" if not is_listed else ("degraded" if waiting is not None and waiting > 20 else "healthy"))
            )
            ttft = per_model("ttft_p95", alias)
            models.append(
                {
                    "alias": alias,
                    "provider": provider["name"],
                    "kind": provider.get("kind", "internal"),
                    "status": status,
                    "listed": is_listed,
                    "requests_running": per_model("requests_running", alias),
                    "requests_waiting": waiting,
                    "ttft_p95_ms": round(ttft * 1000, 1) if ttft is not None else None,
                    "tokens_per_s": per_model("gen_tokens_per_s", alias),
                    "kv_cache_usage": per_model("kv_cache_usage", alias),
                    **probed.get(alias, {}),
                }
            )
    nodes: dict[str, dict[str, Any]] = {}
    for metric, field in (
        ("gpu_util", "gpu_util"),
        ("gpu_mem_used_ratio", "gpu_mem_used_ratio"),
        ("gpu_temp", "gpu_temp_c"),
    ):
        for labels, value in metrics.get(metric, []):
            host = labels.get("Hostname") or labels.get("instance") or labels.get("node")
            if host:
                nodes.setdefault(host, {"name": host})[field] = value
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    tokens_today = int(
        (
            await db.execute(
                select(func.coalesce(func.sum(UsageRecord.tokens_in + UsageRecord.tokens_out), 0)).where(
                    UsageRecord.bucket == "hour", UsageRecord.ts_bucket >= today
                )
            )
        ).scalar_one()
    )
    statuses = {m["status"] for m in models if m["kind"] == "internal"}
    overall = (
        "down"
        if not ok
        else ("degraded" if statuses & {"down", "degraded"} else "healthy")
        if statuses
        else ("healthy" if ok else "unknown")
    )
    result = {
        "status": overall,
        "gateway": {"ok": ok, "latency_ms": latency, "error": error},
        "prometheus_configured": bool(prom),
        "models": models,
        "nodes": sorted(nodes.values(), key=lambda n: n["name"]),
        "prefix_cache_hit_rate": scalar("prefix_cache_hit"),
        "preemptions_24h": scalar("preemptions_24h"),
        "tokens_today": tokens_today,
        "checked_at": datetime.now(UTC),
    }
    _cache[key] = (time.monotonic() + CACHE_S, result)
    return result
