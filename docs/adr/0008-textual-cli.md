# ADR 0008 — Textual for the interactive CLI and `z top`

Status: accepted

## Context
We want a Claude Code-like interactive experience and a k9s-like dashboard, in the same language as the server.

## Decision
Use Typer for subcommands and Textual for the interactive session (inline mode) and `z top` (full screen).

## Consequences
One Python codebase and good testing (snapshots). If inline mode proves limiting, the session can move to prompt_toolkit + Rich without changing commands.
