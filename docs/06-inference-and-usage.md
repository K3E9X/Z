# 06 — Inference, model routing and token usage

## 1. Providers
| Provider | Kind | Default | Data classes allowed |
|---|---|---|---|
| **inferX** | internal, OpenAI-compatible (`glm-5-3-nvfp4`, `qwen3-8-27b-nvfp4`; embeddings when opened), see ADR 0024 | enabled, primary | C0–C3, only for models without an external fallback |
| Anthropic API | external | disabled | C0–C1 if enabled |
| Azure OpenAI | external | disabled | C0–C1 if enabled |
| Mistral API | external | disabled | C0–C1 if enabled |
| Any OpenAI-compatible endpoint (vLLM, Ollama, TGI, LM Studio) | internal or external, set per endpoint | disabled | set per endpoint |

All providers are declared in `config/litellm.yaml` (models) and `config/z.yaml` (policy). Z code only knows aliases.

## 2. Aliases
| Alias | Typical model | Used for |
|---|---|---|
| `z-heavy` | GLM (large) | Planning, code, architecture reviews, learning proposals |
| `z-fast` | Qwen | Triage at scale, extraction, browsing |
| `z-judge` | small instruct model | Injection scan, pre-action checks, routing, classification, eval grading |
| `z-embed` | embedding model | Memory and skill search (pgvector) |

Agents declare `model` and optional `escalate_model`. The mapping alias → model lives in LiteLLM config only. Each alias is gated by `poc/check_tool_calling.py` (not needed for `z-embed`).

## 3. Routing policy (data classes)
Classes are configurable; defaults:
| Class | Meaning | May go to |
|---|---|---|
| C0 | Public | any enabled provider |
| C1 | Internal | internal, or external if the provider allows C1 |
| C2 | Confidential (customer data, infrastructure details) | internal only |
| C3 | Restricted (secrets, credentials, regulated) | internal only, and never stored in memory |

How a call gets its class: the highest of (mission class set by the user or playbook, default `C2`; connector `data_class` of every tool result in context; judge classification of staged inputs). The class only goes up during a mission.

**Enforcement is in the key, not in the runner.** z-server creates each mission's LiteLLM virtual key with an explicit `models` allowlist computed from the class: C2/C3 → internal aliases only; C0/C1 → internal plus the external aliases whose provider allows that class. When the gateway returns a result that raises the class, z-server immediately updates the key's allowlist (or rotates the key) before the next model call. The runner never sends or chooses the class, so a manipulated job cannot widen it. A LiteLLM custom callback that re-checks `metadata.data_class` is optional defence in depth (B6 writes it, B8 mounts it).

External fallback when inferX is degraded is **off** by default and, when on, only for C0–C1 missions.

## 4. Gateway setup (LiteLLM)
- One virtual key per mission (and per job for sub-agents) with `max_budget` in tokens or internal cost, TTL, and metadata `{mission, job, agent, team, playbook}`. Revoked when the mission ends.
- Spend logs give tokens per call; the monitor aggregates them into `UsageRecord` buckets (1 min → 1 h → 1 day).
- Retries and timeouts in LiteLLM router settings; no cross-provider fallback unless the routing policy allows it.
- Prompt caching: keep system prompts and skills stable at the start of the context so the server's prefix cache works (vLLM automatic prefix caching).

Check LiteLLM's current docs for key generation, metadata, spend endpoints and callbacks before coding.

## 5. inferX health (Inference screen and `z top`)
Z does not assume how inferX is built. The monitor reads a **Prometheus** endpoint with queries from `config/z.yaml`, with defaults for vLLM + NVIDIA DCGM exporters:

| Panel | Default query (verify names against your exporters) |
|---|---|
| Requests running / waiting per model | `vllm:num_requests_running`, `vllm:num_requests_waiting` by `model_name` |
| KV cache usage | `vllm:gpu_cache_usage_perc` (newer vLLM: `vllm:kv_cache_usage_perc`) |
| Prefix cache hit rate | `rate(vllm:prefix_cache_hits_total[5m]) / rate(vllm:prefix_cache_queries_total[5m])` |
| p95 time to first token | `histogram_quantile(0.95, sum by (le, model_name) (rate(vllm:time_to_first_token_seconds_bucket[5m])))` |
| Throughput | `rate(vllm:generation_tokens_total[1m])`, `rate(vllm:prompt_tokens_total[1m])` |
| Preemptions | `increase(vllm:num_preemptions_total[24h])` |
| GPU utilization, memory, temperature | `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED / (DCGM_FI_DEV_FB_USED + DCGM_FI_DEV_FB_FREE)`, `DCGM_FI_DEV_GPU_TEMP` by node |
| Replicas ready | `kube_deployment_status_replicas_available` (kube-state-metrics), if the cluster is visible |

Also: `GET /v1/models` on the gateway every minute, and a synthetic tool-call probe per alias every 10 minutes (records `tool_call_parse_error_rate`). If no Prometheus is configured, the screen shows gateway-side numbers only (latency, errors, tokens from LiteLLM).

## 6. Usage and budgets
- **Views**: tokens per hour by model (stacked), by agent, top missions with used/cap, by team, by playbook; input/output split; cache hit; requests and errors; GPU-hours when available.
- **Budgets** at org, team, user, playbook, mission, agent, objective. Each has `limit`, `period` (`mission`, `day`, `week`, `month`), `alert_at` (default 80 %). Hard limits stop new jobs; running jobs finish their current step then stop with `budget_exceeded`.
- **Alerts** to Review (banner), notifications and the event feed.
- **CLI**: `z usage --by agent|model|mission|team --since 7d [--json]`.
- **Export**: CSV/JSON; Prometheus metrics on `z-server /metrics` (bearer `Z_METRICS_TOKEN`), read from the database at
  each scrape: `z_tokens_total{model,agent,team,direction}`, `z_missions_total{status}` (ended missions, by final
  status), `z_missions_active{status}`, `z_jobs{status}` (not ended), `z_review_pending{kind}` (action, question),
  `z_connector_up{connector}`, `z_notifications_pending`. Every replica reports the same values: aggregate with `max`.
  The chart ships a "Z overview" Grafana dashboard (`metrics.grafanaDashboard.enabled`).

## 7. Cost model
Internal tokens have no invoice, but GPU time is not free. `config/z.yaml` can set an internal cost per 1M input/output tokens per alias so budgets and analytics can show a comparable cost, and external providers their real price.
