# ADR 0027 — The inferX key can be set from Z, and lives in the model gateway only

Status: accepted

## Context
ADR 0024 gives LiteLLM the only inferX key (invariant 3), from the deployment's `INFERENCE_API_KEY`. Changing it
meant editing a Secret or `.env` and restarting LiteLLM. The owner asked to set it from the web app. The key must
still never reach agents, prompts, events, logs, the audit or Z's own database in clear (invariants 3 and 11), and
a key typed in the browser must not be shown again.

## Decision
1. **LiteLLM's credentials API, verified on v1.105.0.** The inferX deployments name a credential
   (`litellm_credential_name: inferx`) instead of carrying `api_key`. `credential_list` in the LiteLLM config
   defines `inferx` from `INFERENCE_API_KEY`, the default. z-server replaces it with `PATCH /credentials/inferx`
   (or `POST /credentials` when it is only in the config), authenticated with the master key. LiteLLM resolves the
   credential at each call, so the new key is used from the next request without a restart.
2. **Persistence.** LiteLLM stores the credential encrypted in its own database with `LITELLM_SALT_KEY` (kept
   once generated, like `Z_SECRET_KEY`) and reloads it at start; this needs `STORE_MODEL_IN_DB=True` (Z adds no
   model through the API). The stored credential wins over the environment value, also after a restart.
3. **Z keeps no key.** `PUT /inference/credential` (permission `providers:manage`, step-up, audited) takes the
   key once, checks its format (`ixk_` + 64 hex), sends it to LiteLLM and keeps only who set it, when, and the
   result of a check (one `GET /health?model=z-fast` through the gateway, error text redacted). The key's
   fingerprint (first 12 hex of SHA-256) is stored in the credential's `credential_info`, so the status Z shows
   comes from what LiteLLM serves. `GET /inference/credential` never returns the key; validation errors never
   echo it; the audit records the fingerprint only.
4. **UI.** Inference → Model providers → inferX key: a password field, cleared once sent, with the source (set
   from Z, from the deployment, none), the fingerprint and the last check.

## Consequences
- Rotating the inferX key is an admin action in Z, effective at once, audited, with a check on a real model.
- `LITELLM_SALT_KEY` must never change (the stored key could not be decrypted); deployments with an existing
  Secret without it fall back to the master key, which then must not be rotated.
- Going back to the environment value means deleting the `inferx` credential in LiteLLM and restarting it.
- Verified end to end on the litellm 1.105.0 image with Postgres: the environment key by default; the key set
  through the API from the next call, after a restart, and when replaced again; a wrong key reported by the
  check (`key_inert`) without the key in the error; the value encrypted in LiteLLM's database.
