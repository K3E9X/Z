# ADR 0031 — Retention: an hourly sweep of ended missions; the audit log stays with the database owner

Status: accepted

## Context
docs/07 §6 sets retention for mission data (events 180 days, artifacts 90 days, C3 artifacts 7 days) and
docs/12 §4 keeps the audit log 365 days (minimum 90). The `retention.*` keys were in the config schema, but nothing
applied them: C3 artifacts stayed forever. The audit tables are append-only for the application's database role
(invariant 9): the application cannot delete from them, by design.

## Decision
1. A `retention` worker in z-server sweeps every hour. It removes the events of missions that ended more than
   `events_days` ago, and the artifacts (file first, then row) of ended missions older than `artifacts_days`, or
   `c3_artifacts_days` for C3 artifacts. Missions that still run keep everything; deletions are idempotent, so
   every replica may sweep.
2. The audit log is not swept by the application. Its retention is applied by the database owner (the migration
   role), outside Z: archive the rows older than the retention (the forwarding to the SIEM already holds a copy),
   then delete them in a maintenance window **up to and including the `seq` of a signed checkpoint**
   (`audit_checkpoints`). `audit.verify` accepts a chain whose first remaining event continues a signed
   checkpoint; a purge that stops anywhere else is reported as tampering.

## Consequences
- C3 data leaves the platform after 7 days once its mission has ended, as the spec promises.
- Mission timelines older than 180 days show the mission without its events.
- `audit.retention_days` (Admin → Audit) documents the policy for the database owner; it does not delete by itself.
