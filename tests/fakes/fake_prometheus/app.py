"""Fake Prometheus HTTP API returning plausible vLLM / DCGM / kube-state-metrics data."""

from __future__ import annotations

import math
import re
import threading
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MODELS = ["z-heavy", "z-fast", "z-judge"]
NODES = ["gpu-node-1", "gpu-node-2", "gpu-node-3"]

# series key -> (label name, {label value: sample value})
SERIES: dict[str, tuple[str, dict[str, float]]] = {
    "requests_running": ("model_name", {"z-heavy": 6, "z-fast": 14, "z-judge": 2}),
    "requests_waiting": ("model_name", {"z-heavy": 1, "z-fast": 3, "z-judge": 0}),
    "kv_cache_usage": ("model_name", {"z-heavy": 0.71, "z-fast": 0.48, "z-judge": 0.22}),
    "prefix_cache_hit": ("model_name", {"z-heavy": 0.64, "z-fast": 0.58, "z-judge": 0.81}),
    "prefix_cache_hits_total": ("model_name", {"z-heavy": 1.9e7, "z-fast": 4.1e7, "z-judge": 6.5e6}),
    "prefix_cache_queries_total": ("model_name", {"z-heavy": 2.97e7, "z-fast": 7.07e7, "z-judge": 8.0e6}),
    "ttft_p95": ("model_name", {"z-heavy": 0.85, "z-fast": 0.32, "z-judge": 0.09}),
    "ttft_bucket": ("model_name", {"z-heavy": 1200, "z-fast": 5400, "z-judge": 900}),
    "gen_tokens_per_s": ("model_name", {"z-heavy": 412.5, "z-fast": 1280.0, "z-judge": 96.0}),
    "prompt_tokens_per_s": ("model_name", {"z-heavy": 2150.0, "z-fast": 5400.0, "z-judge": 800.0}),
    "generation_tokens_total": ("model_name", {"z-heavy": 8.2e8, "z-fast": 2.4e9, "z-judge": 1.1e8}),
    "prompt_tokens_total": ("model_name", {"z-heavy": 4.1e9, "z-fast": 9.8e9, "z-judge": 7.7e8}),
    "preemptions_24h": ("model_name", {"z-heavy": 3, "z-fast": 11, "z-judge": 0}),
    "preemptions_total": ("model_name", {"z-heavy": 120, "z-fast": 431, "z-judge": 2}),
    "gpu_util": ("Hostname", {"gpu-node-1": 87, "gpu-node-2": 64, "gpu-node-3": 23}),
    "fb_used": ("Hostname", {"gpu-node-1": 71000, "gpu-node-2": 52000, "gpu-node-3": 18000}),
    "fb_free": ("Hostname", {"gpu-node-1": 10920, "gpu-node-2": 29920, "gpu-node-3": 63920}),
    "gpu_mem_used_ratio": ("Hostname", {"gpu-node-1": 0.8667, "gpu-node-2": 0.6348, "gpu-node-3": 0.2197}),
    "gpu_temp": ("Hostname", {"gpu-node-1": 71, "gpu-node-2": 64, "gpu-node-3": 52}),
    "replicas_available": ("deployment", {"vllm-z-heavy": 2, "vllm-z-fast": 3, "vllm-z-judge": 1}),
}

# Aggregate (no `by`) values that are not a plain sum.
AGGREGATE: dict[str, float] = {"prefix_cache_hit": 0.62, "kv_cache_usage": 0.47, "ttft_p95": 0.41}

# Ordered classification: first pattern found in the query wins.
CLASSIFY: list[tuple[str, str]] = [
    (r"prefix_cache_hits_total.*/.*prefix_cache_queries_total", "prefix_cache_hit"),
    (r"histogram_quantile\(.*time_to_first_token_seconds_bucket", "ttft_p95"),
    (r"DCGM_FI_DEV_FB_USED.*/.*DCGM_FI_DEV_FB_FREE", "gpu_mem_used_ratio"),
    (r"rate\(\s*vllm:generation_tokens_total", "gen_tokens_per_s"),
    (r"rate\(\s*vllm:prompt_tokens_total", "prompt_tokens_per_s"),
    (r"increase\(\s*vllm:num_preemptions_total", "preemptions_24h"),
    (r"vllm:num_requests_running", "requests_running"),
    (r"vllm:num_requests_waiting", "requests_waiting"),
    (r"vllm:(gpu|kv)_cache_usage_perc", "kv_cache_usage"),
    (r"vllm:prefix_cache_hits_total", "prefix_cache_hits_total"),
    (r"vllm:prefix_cache_queries_total", "prefix_cache_queries_total"),
    (r"vllm:time_to_first_token_seconds_bucket", "ttft_bucket"),
    (r"vllm:generation_tokens_total", "generation_tokens_total"),
    (r"vllm:prompt_tokens_total", "prompt_tokens_total"),
    (r"vllm:num_preemptions_total", "preemptions_total"),
    (r"DCGM_FI_DEV_GPU_UTIL", "gpu_util"),
    (r"DCGM_FI_DEV_FB_USED", "fb_used"),
    (r"DCGM_FI_DEV_FB_FREE", "fb_free"),
    (r"DCGM_FI_DEV_GPU_TEMP", "gpu_temp"),
    (r"kube_deployment_status_replicas_available", "replicas_available"),
]

AGG_FUNCS = re.compile(r"\b(sum|avg|max|min|count|histogram_quantile|topk|bottomk)\s*(by|\()")


def classify(query: str) -> str | None:
    for pattern, key in CLASSIFY:
        if re.search(pattern, query):
            return key
    return None


def _grouping(query: str) -> set[str]:
    labels: set[str] = set()
    for group in re.findall(r"\bby\s*\(([^)]*)\)", query):
        labels.update(x.strip() for x in group.split(",") if x.strip())
    labels.discard("le")
    return labels


def _base_labels(key: str, label: str, value: str) -> dict[str, str]:
    if label == "model_name":
        return {"model_name": value, "instance": f"vllm-{value}:8000", "job": "vllm"}
    if label == "Hostname":
        return {"Hostname": value, "gpu": "0", "modelName": "NVIDIA H100 80GB HBM3", "job": "dcgm-exporter"}
    return {"deployment": value, "namespace": "inferx", "job": "kube-state-metrics"}


class PromState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.down = False
        self.overrides: dict[str, Any] = {}

    def series(self, query: str) -> list[tuple[dict[str, str], float]]:
        """Evaluate a query to (labels, value) pairs; unknown -> []."""
        with self.lock:
            exact = self.overrides.get(query)
            overrides = dict(self.overrides)
        if isinstance(exact, list):
            return [(dict(s.get("metric", {})), float(s["value"])) for s in exact]
        key = classify(query)
        if key is None:
            return []
        label, values = SERIES[key]
        values = dict(values)
        override = exact if exact is not None else overrides.get(key)
        if isinstance(override, int | float):
            values = dict.fromkeys(values, float(override))
        elif isinstance(override, dict):
            values.update({k: float(v) for k, v in override.items()})
        selectors = dict(re.findall(r'(model_name|Hostname|deployment)\s*=\s*"([^"]+)"', query))
        if label in selectors:
            values = {k: v for k, v in values.items() if k == selectors[label]}
        aggregated = AGG_FUNCS.search(query) is not None or "/" in query
        if label in _grouping(query):
            return [({label: k}, v) for k, v in values.items()]
        if aggregated:
            if not values:
                return []
            if isinstance(override, int | float):
                return [({}, float(override))]
            agg = AGGREGATE.get(key) if override is None else None
            if agg is None:
                agg = sum(values.values())
                if key in ("gpu_util", "gpu_temp", "gpu_mem_used_ratio", "kv_cache_usage", "prefix_cache_hit"):
                    agg = agg / len(values)
            return [({}, round(agg, 4))]
        name = re.search(r"[A-Za-z_:][A-Za-z0-9_:]*", query)
        metric = name.group(0) if name else key
        return [({"__name__": metric, **_base_labels(key, label, k)}, v) for k, v in values.items()]


def _fmt(value: float) -> str:
    v = float(value)
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)


def _parse_time(raw: str | None, default: float) -> float:
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        from datetime import datetime

        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()


def _parse_step(raw: str | None) -> float:
    if not raw:
        return 60.0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)?", raw.strip())
    if not m:
        raise ValueError(f"invalid step {raw!r}")
    mult = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, None: 1}[m.group(2)]
    return float(m.group(1)) * mult


def create_app() -> FastAPI:
    state = PromState()
    app = FastAPI(title="fake-prometheus")
    app.state.fake = state

    def bad(message: str, status: int = 400) -> JSONResponse:
        return JSONResponse({"status": "error", "errorType": "bad_data", "error": message}, status_code=status)

    def unavailable() -> JSONResponse:
        return JSONResponse({"status": "error", "errorType": "unavailable", "error": "fake outage"}, status_code=503)

    async def params(request: Request) -> dict[str, str]:
        values = dict(request.query_params)
        if request.method == "POST":
            form = await request.form()
            values.update({k: str(v) for k, v in form.items()})
        return values

    @app.api_route("/api/v1/query", methods=["GET", "POST"])
    async def query(request: Request) -> JSONResponse:
        if state.down:
            return unavailable()
        p = await params(request)
        if not p.get("query"):
            return bad("missing query")
        try:
            ts = _parse_time(p.get("time"), time.time())
        except ValueError as exc:
            return bad(str(exc))
        result = [{"metric": labels, "value": [ts, _fmt(v)]} for labels, v in state.series(p["query"])]
        return JSONResponse({"status": "success", "data": {"resultType": "vector", "result": result}})

    @app.api_route("/api/v1/query_range", methods=["GET", "POST"])
    async def query_range(request: Request) -> JSONResponse:
        if state.down:
            return unavailable()
        p = await params(request)
        if not p.get("query"):
            return bad("missing query")
        try:
            now = time.time()
            start = _parse_time(p.get("start"), now - 3600)
            end = _parse_time(p.get("end"), now)
            step = _parse_step(p.get("step"))
        except ValueError as exc:
            return bad(str(exc))
        if step <= 0 or end < start or (end - start) / step > 11000:
            return bad("exceeded maximum resolution of 11,000 points per timeseries, or bad range")
        points = [start + i * step for i in range(int((end - start) // step) + 1)]
        result = []
        for labels, v in state.series(p["query"]):
            values = [[t, _fmt(round(v * (1 + 0.1 * math.sin(t / 600.0)), 4))] for t in points]
            result.append({"metric": labels, "values": values})
        return JSONResponse({"status": "success", "data": {"resultType": "matrix", "result": result}})

    @app.get("/-/healthy")
    @app.get("/-/ready")
    def healthy() -> JSONResponse:
        return unavailable() if state.down else JSONResponse({"status": "ok"})

    # ---- control API -------------------------------------------------------
    @app.post("/_fake/values")
    async def set_values(request: Request) -> dict[str, Any]:
        """Body: {key: number | {label_value: number} | [{metric, value}]}; key = series key or exact query."""
        body = await request.json()
        with state.lock:
            state.overrides.update(body)
            return {"overrides": state.overrides}

    @app.delete("/_fake/values")
    def clear_values() -> dict[str, bool]:
        with state.lock:
            state.overrides.clear()
        return {"ok": True}

    @app.post("/_fake/down")
    async def set_down(request: Request) -> dict[str, bool]:
        body = await request.body()
        state.down = bool((await request.json()).get("down", True)) if body else True
        return {"down": state.down}

    @app.post("/_fake/up")
    def set_up() -> dict[str, bool]:
        state.down = False
        return {"down": False}

    @app.post("/_fake/reset")
    def reset() -> dict[str, bool]:
        with state.lock:
            state.overrides.clear()
        state.down = False
        return {"ok": True}

    return app
