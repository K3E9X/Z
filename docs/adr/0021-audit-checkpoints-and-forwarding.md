# ADR 0021 — Audit checkpoints and forwarding run in one leader-elected worker

Status: accepted

## Context
docs/12 §4 asks for a signed checkpoint every hour, written to the log and to the forwarder, and continuous
forwarding as syslog (RFC 5424 over TLS) or JSON over HTTPS. It does not say how delivery survives a log platform
outage, what a newly configured destination receives, or how several z-server replicas avoid duplicates.

## Decision
1. **One worker per tick.** `workers/audit.py` runs in every z-server that runs workers; each tick takes a
   transaction-scoped advisory lock (`pg_try_advisory_xact_lock`), so one replica works and the others skip.
2. **Checkpoints.** When `checkpoint_minutes` (default 60) have passed since the last checkpoint and events were
   written since, (seq, hash) of the head is signed with the server key and recorded as an `audit.checkpoint`
   event. The checkpoint runs in its own short transaction: no network I/O while the chain lock is held.
3. **Forwarding is at-least-once and in order.** Each target keeps a cursor (last forwarded seq) in `settings`
   (`audit_forward_state`); batches of 500 events are sent after it, and the cursor moves only when the target
   accepted the batch. Every line carries `seq` and `hash` so the log platform can drop duplicates. The last
   error and the cursor are shown read-only as `forward.<target>.status` in `GET /admin/audit-settings`.
4. **A new destination starts from now** (its cursor is the current head) unless `from_now: false` asks for a
   backfill; this avoids flooding a SIEM with the whole history by accident.
5. **Transports.** syslog: `tls://host:port` only (no plain TCP or UDP), octet-counted framing (RFC 6587
   §3.4.1), the system trust store plus `SSL_CERT_FILE` for an internal CA. HTTPS: POST
   `application/x-ndjson`, no redirects, optional bearer token from a secret reference (`token_ref`), never a
   value. Both URLs pass the SSRF guard when saved and again before each delivery.
6. **Retention is not deleted by the app.** The app role cannot delete audit rows (invariant 9); purging after
   `retention_days` is an operator job with the owner role (see deploy/README.md).

## Consequences
- An outage of the log platform delays forwarding but loses nothing; the admin sees the failure in the Audit tab.
- A slow destination holds one database connection for at most a few batches per tick, never the chain lock.
