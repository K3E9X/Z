# ADR 0033 — Connector secrets typed in the web app, sealed with the platform key

Status: accepted

## Context
Connector credentials are references to a secrets backend (`env:`, `file:`, `vault:`, `kubernetes:`): docs/05 §4
says "credentials are references in the database, never values". Setting one up means editing the deployment
(an environment variable, a mounted file, a Vault path) and restarting or waiting for the cache. Admins asked to
type a connector secret in the web app (setConnectorCredential / clearConnectorCredential in the contract), the
way personal OAuth grants are already stored: sealed with the platform key in the `credentials` table
(`gateway/oauth.py`, `backend = "sealed"`).

The constraints are the security invariants: agents hold no credentials (3), read and write credentials are
always separate (7), secrets never appear in prompts, events, artifacts, logs or traces (11), every mutating
request is audited (9).

## Decision
1. **Storage.** Migration 0006 adds a nullable `credentials.sealed_value` (expand-only). A typed secret is sealed
   with the platform key (`state.sealer`, AES-GCM, associated data `connector-secret:<credential id>` so a blob
   cannot be moved to another row) on the **service** credential row of that connector and purpose. The row's
   `backend` becomes `sealed`, its `ref` the non-secret marker `sealed:<connector>/<purpose>`, `rotated_at` the
   time it was typed; an optional `username` replaces the identity (basic auth account).
2. **Who and which purposes.** `connectors:manage` with step-up (contract). Only `read` or `write`, only for a
   connector whose manifest declares that purpose with a static secret type (`bearer`, `api_key`, `basic`); scoped
   OAuth flows and `none` are refused (Problem `validation`), as are unknown connectors. The value is a
   `SecretStr` (1 to 16384 characters, contract); a trailing newline is dropped. Typing a write secret equal to
   the read one (typed, or the manifest reference when it resolves) is refused (invariant 7).
3. **Use.** The gateway's `SecretResolver` gets a `sealed` lookup (installed by `Gateway.create`) and a
   `typed(connector, purpose)` method. The credential injection (`gateway.auth.material`), which every service
   call goes through (agent reads, the action executor's writes, server-side clones, MCP discovery), asks for the
   typed secret **of the purpose it injects** and uses it in place of the manifest reference. So the read secret
   only ever reaches read calls and the write secret only the executor: the separation is the one manifests
   already have. Delegated (user) OAuth grants are untouched.
4. **Caching.** The lookup is cached like other secrets (`CACHE_TTL_S`, 60 s, misses included). Setting or
   clearing a secret clears the resolver cache of the replica that served the request after the commit; other
   replicas pick the change up within the TTL (the same window as an environment or Vault rotation).
5. **Manifest sync.** `sync_credentials` (pack seed, connector create and manifest edit) keeps a typed secret as
   long as the manifest still declares a static secret for its purpose: a re-seed or a manifest edit never
   replaces it silently. A manifest that drops the purpose, or turns it into OAuth or `none`, drops the typed
   secret with the row it no longer needs.
6. **Clearing.** clearConnectorCredential drops the sealed value and restores the manifest's reference, backend
   and identity (`rotated_at` = now), or deletes the row when the manifest has no reference for that purpose.
   Clearing a purpose with no typed secret is `not-found`.
7. **Never shown.** The value is never returned (GET /connectors shows `backend: sealed` and the marker), never
   logged, never put in an event. The audit event (`connector.secret_set`, `connector.secret_cleared`) records the
   connector, the purpose, the credential, the identity, who (the actor) and, for a set, a fingerprint: a keyed,
   truncated HMAC that tells two values apart without revealing either.

## Consequences
- An admin can configure or rotate a connector secret without touching the deployment; secrets backends stay the
  recommended path for production (rotation from Vault, no secret in the database backups).
- The database now holds some connector secret values, encrypted. Their confidentiality rests on `Z_SECRET_KEY`,
  like personal OAuth tokens, TOTP seeds and mission keys already do; rotating `Z_SECRET_KEY` means typing these
  secrets again.
- docs/05 §4 "credentials are references in the database, never values" now has this exception: a sealed value
  typed by an admin.
