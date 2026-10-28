# ADR 0002 — LiteLLM is the only path to any model

Status: accepted

## Context
Data must stay on the internal cluster by default, usage must be measured per mission and agent, and external providers must be optional and policed.

## Decision
All model calls go through a LiteLLM proxy with aliases (`z-heavy`, `z-fast`, `z-judge`, `z-embed`), one virtual key per mission, budgets and spend logs. A Z routing check enforces data classes before every call.

## Consequences
One place for egress control, accounting and provider changes. LiteLLM becomes a critical dependency; it is deployed with the chart and monitored.
