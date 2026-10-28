# ADR 0005 — Writes are proposals applied by a separate executor

Status: accepted

## Context
Every external write must be approved by a human, and agents must not hold write credentials.

## Decision
Agents call `propose_action` with a validated payload and preview. Approved actions are applied by `z-server executor` with write credentials and an idempotency key. Destructive actions need two approvers.

## Consequences
Slower than autonomous agents, but safe and auditable. Auto-approval for narrow reversible operations is a v2 option, off by default.
