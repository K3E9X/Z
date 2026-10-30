# ADR 0004 — Postgres queue and checkpoints in v1

Status: accepted

## Context
We need durable jobs, concurrency control and resume after crash, with minimal infrastructure.

## Decision
Use Postgres (`SKIP LOCKED`, LISTEN/NOTIFY) for the queue and store checkpoints after each agent step.

## Consequences
No extra broker. If throughput or workflow complexity outgrows it, move to Temporal or Hatchet behind the scheduler interface.
