# ADR 0006 — Skills use the AgentSkills `SKILL.md` format

Status: accepted

## Context
Skills should be portable between harnesses and reusable from existing collections.

## Decision
Use the AgentSkills format (front matter + instructions + optional scripts and references). Z adds optional `version`, `evals`, `owner` keys.

## Consequences
Skills work in OpenHands, Claude Code and other harnesses. Z versions and evaluates them.
