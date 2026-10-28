# ADR 0001 — OpenHands Software Agent SDK as the agent harness

Status: accepted

## Context
Agents must run on internal open-weight models (GLM, Qwen) through an OpenAI-compatible API. We need a mature agent loop with tools, MCP, skills, workspaces and a security analyzer.

## Decision
Use the OpenHands Software Agent SDK (MIT, model-agnostic) inside `z-runner`, behind an `AgentBackend` interface. The rest of Z never imports the SDK.

## Consequences
We inherit its loop and tools and keep the option to add another backend (Claude Agent SDK, a custom loop) without touching the server, CLI or UI. We must track SDK releases and verify APIs.
