"""Probe the inference cluster for what the Z Inference screen can show.

Usage:
    uv run --with httpx poc/check_inferx_metrics.py --prometheus URL [--gateway URL --api-key KEY]

1. Lists the models served by the OpenAI-compatible gateway (/v1/models), if given.
2. Runs each default Prometheus query from config/z.example.yaml and reports which return data,
   so you can adjust `inference.prometheus.queries` in config/z.yaml before building.
Exit code 0 if at least the core queries return data, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys

import httpx

QUERIES = {
    "requests_running": "sum by (model_name) (vllm:num_requests_running)",
    "requests_waiting": "sum by (model_name) (vllm:num_requests_waiting)",
    "kv_cache_usage (old name)": "avg by (model_name) (vllm:gpu_cache_usage_perc)",
    "kv_cache_usage (new name)": "avg by (model_name) (vllm:kv_cache_usage_perc)",
    "ttft_p95": "histogram_quantile(0.95, sum by (le, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))",
    "gen_tokens_per_s": "sum by (model_name) (rate(vllm:generation_tokens_total[1m]))",
    "prompt_tokens_per_s": "sum by (model_name) (rate(vllm:prompt_tokens_total[1m]))",
    "prefix_cache_hit": "sum(rate(vllm:prefix_cache_hits_total[5m])) / sum(rate(vllm:prefix_cache_queries_total[5m]))",
    "preemptions_24h": "sum(increase(vllm:num_preemptions_total[24h]))",
    "gpu_util": "avg by (Hostname) (DCGM_FI_DEV_GPU_UTIL)",
    "gpu_mem_used": "sum by (Hostname) (DCGM_FI_DEV_FB_USED)",
    "gpu_temp": "max by (Hostname) (DCGM_FI_DEV_GPU_TEMP)",
}
CORE = {"requests_running", "gen_tokens_per_s"}


def check_models(gateway: str, api_key: str) -> None:
    try:
        r = httpx.get(f"{gateway.rstrip('/')}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
        r.raise_for_status()
        ids = [m.get("id") for m in r.json().get("data", [])]
        print(f"[OK] /v1/models: {', '.join(ids) or '(none)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] /v1/models: {exc}")


def check_queries(prometheus: str) -> bool:
    ok_core = set()
    for name, q in QUERIES.items():
        try:
            r = httpx.get(f"{prometheus.rstrip('/')}/api/v1/query", params={"query": q}, timeout=15)
            r.raise_for_status()
            result = r.json().get("data", {}).get("result", [])
            if result:
                sample = result[0].get("value", [None, "?"])[1]
                print(f"[OK]   {name}: {len(result)} series, e.g. {sample}")
                ok_core.add(name)
            else:
                print(f"[MISS] {name}: no data (metric name may differ on your exporter)")
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {name}: {exc}")
    return CORE <= ok_core


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--prometheus", required=True)
    p.add_argument("--gateway", help="OpenAI-compatible base URL ending in /v1")
    p.add_argument("--api-key", default="")
    a = p.parse_args()
    if a.gateway:
        check_models(a.gateway, a.api_key)
    passed = check_queries(a.prometheus)
    print("RESULT:", "core inference metrics available" if passed else "adjust queries in config/z.yaml")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
