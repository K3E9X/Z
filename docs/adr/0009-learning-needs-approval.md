# ADR 0009 — The learning loop proposes; humans apply

Status: accepted

## Context
Self-improving agents can drift or be poisoned through crafted signals.

## Decision
The learner drafts proposals, runs evals and shadow replay, discards any regression and waits for human approval. Every applied change is versioned and reversible.

## Consequences
Slower improvement, but trustworthy and auditable.
