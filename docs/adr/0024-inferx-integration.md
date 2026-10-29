# ADR 0024 — Integration with inferX (the internal inference platform)

Status: accepted (from the inferX user documentation provided on 2026-10-29)

## Context
inferX is the internal, OpenAI-compatible inference platform. What matters for Z:
- Base URL `https://inferx.datax.iliad.fr/inference/v1` (public gateway, IP-filtered) or
  `https://private.inferx.datax.iliad.fr/inference/v1` (linked Scaleway VPC or WireGuard, no Internet path).
- Keys `ixk_` + 64 hex, shown once, SHA-256 stored only; per key: allowed models, expiry (90 days, 1 year,
  never), optional requests/s and tokens/day limits. Organisation keys need `x-inferx-project`.
- Catalogue (2026-10-07): `glm-5-3-nvfp4` (category `pro`, 500k context, tools, reasoning, 1.50/5.50 EUR per M
  tokens), `qwen3-8-27b-nvfp4` (category `medium`, vision, 1.00/2.00), dedicated-only models, a decision model
  `mammoth-1` (`/v1/systemone`), voice models. No shared embeddings model.
- Errors: 401 `invalid_api_key`, 403 `key_inert` / `project_required`, 404 `model_not_found`, 429
  `rate_limit_exceeded` and 503 `model_unavailable` / `outside_time_window` / `no_capacity_available` /
  `maintenance`, both with `Retry-After`. No `x-ratelimit-*` headers.
- inferX never stores prompts or answers (traces keep metadata 30 days). **But** a deployment may overflow to
  an external provider ("secours externe", response header `x-inferx-fallback: genapi`), with the content.

## Decision
1. **LiteLLM holds the only inferX key** (invariant 3): one *project* key per Z environment, allowed exactly
   the models of `config/litellm.yaml`, expiry 1 year with a rotation reminder, and a tokens/day limit set at the
   organisation's monthly budget / 30 as a backstop. Z's per-mission LiteLLM virtual keys stay the enforcement
   point for data classes (invariant 10). Prefer the private gateway.
2. **Aliases are pinned to model ids, not categories**: `z-heavy` → `glm-5-3-nvfp4`, `z-fast` and `z-judge` →
   `qwen3-8-27b-nvfp4`. A category may change model under Z without review, which would invalidate eval
   baselines and learning proposals (invariant 14); changing a model is a reviewed config change.
3. **No external fallback on anything Z calls** (invariant 2): every model the Z key can call must be shared
   without overflow, or a dedicated deployment with no fallback or the shared-model fallback. This is a
   requirement on the inferX project, checked at go-live; LiteLLM returns provider headers as
   `llm_provider-*` (`return_response_headers: true`), so a later Z check can flag
   `llm_provider-x-inferx-fallback` per call. Until that check exists, the requirement is procedural.
4. **Transient errors are retried twice by the LiteLLM router (which honours `Retry-After`); then the runner
   reports `llm_unavailable` and Z requeues the job from its last checkpoint with backoff** until its attempts
   run out. `key_inert`, `model_not_found`, `invalid_api_key` and `project_required` are configuration errors:
   no retry, the job fails with the message, an admin fixes the key.
5. **Secrets**: `ixk_` keys are redacted everywhere (`z_common.redaction`) and caught by gitleaks
   (`.gitleaks.toml`).
6. Costs on the Inference and Usage screens use the catalogue prices (`inference.internal_cost_per_mtok`).

## Consequences
- Memory search (`z-embed`) stays off until an embeddings model is opened to the project; missions run without
  memory facts (`MemoryUnavailable` is already handled).
- Later, with a dedicated deployment: `mammoth-1` is a good fit for the judge (yes/no injection checks with a
  probability, routing to a team), through a LiteLLM pass-through route; the Batch API (`/v1/batches`,
  pre-emptible, cheaper) fits eval runs and shadow replays; `x-inferx-priority: low` fits background objectives.
