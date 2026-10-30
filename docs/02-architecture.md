# 02 — Architecture

## 1. Components

```
 Web app (React)   z CLI / z top (Textual)   Other agents (MCP)   Teams · Slack · Mattermost · mail · webhooks
        │                      │                          │                     │
        └──────────── REST + SSE ─────────────┬───────────┴─────────────────────┘
                                              │
 ┌────────────────────────────────────────────▼──────────────────────────────────────┐
 │ z-server (one Python codebase, one image, several entrypoints)                     │
 │                                                                                    │
 │  api         GitLab OIDC sign-in, sessions, RBAC, admin, audit log,                │
 │              missions, review, agents, skills, playbooks, connectors,              │
 │              usage, SSE streams, MCP server                                        │
 │  scheduler   Postgres queue, triggers (cron, Jira, GitLab, webhooks), planner      │
 │              hand-off, concurrency, budgets, retries, resume after crash           │
 │  gateway     tool gateway: one MCP endpoint per job; credential injection,         │
 │              operation allowlist, rate limits, redaction, judge scan, audit        │
 │  executor    applies approved writes with write credentials, idempotent            │
 │  learner     signals → proposals → evals / shadow replay → waits for approval      │
 │  monitor     inferX health (Prometheus), LiteLLM spend, connector health           │
 └───────┬───────────────────────────┬────────────────────────────┬───────────────────┘
         │ Executor interface        │ MCP (streamable HTTP)      │ admin API
 ┌───────▼────────┐  ┌───────────────▼──────┐            ┌────────▼────────┐
 │ DockerExecutor │  │ KubernetesExecutor   │            │ LiteLLM gateway │──► inferX (GLM, Qwen,
 │ ProcessExecutor│  │ one Job per agent job│            │ aliases, keys,  │    judge, embeddings)
 │ (dev only)     │  │ gVisor optional      │            │ budgets, spend  │──► external APIs
 └───────┬────────┘  └───────────┬──────────┘            └────────▲────────┘    (opt-in, C0–C1)
         └───────────┬───────────┘                                │
             ┌───────▼────────┐   model calls only ───────────────┘
             │ z-runner       │   tool calls only ──► z-server gateway ──► Jira, GitLab, Logos,
             │ AgentBackend → │                                          DefectDojo, Confluence,
             │ OpenHands SDK  │                                          browser, CLI, DB…
             └────────────────┘

 Postgres 16 + pgvector (databases `z` and `litellm`) · artifacts on volume/PVC or S3 · Prometheus /metrics
```

### Why a single server codebase
One image, one migration path, one config. Entrypoints (`z-server api|scheduler|gateway|executor|learner|monitor|all`) scale independently on Kubernetes and run as one process (`all`) in Compose.

### Why a tool gateway
Agents never see credentials or raw endpoints. The runner gets exactly one MCP server, `z-gateway`, authenticated with a short-lived job token. It lists only the operations the agent is allowed to use for this job. Every call is checked, credentialed, rate-limited, redacted, scanned and logged in one place. This is the same idea as Composio or Arcade auth, built in.

## 2. Main flows

### Mission
1. A user (web, CLI, API, MCP) or a trigger creates a **Mission** from a prompt or a playbook.
2. The **planner** (orchestrator agent, `z-heavy`) proposes a **Plan**: steps, agents, inputs, fan-out, writes. Playbooks ship a fixed plan; free-form missions get a drafted plan the user can edit before start.
3. The **scheduler** turns steps into **Jobs** (one per agent per input), respects concurrency and budgets, and hands them to the executor.
4. Each **z-runner** job loads its agent profile, skills and memory, talks to LiteLLM for models and to the gateway for tools, streams **Events**, writes **Artifacts**, and calls `propose_action` for any write.
5. **Review** collects proposed actions and questions. Approvals go to the **executor**, which applies them through the gateway with write credentials.
6. The mission ends; signals feed the **learner**.

### Durable execution
Job state, plan progress and the agent conversation are checkpointed in Postgres after each step. A crashed runner or a restarted pod resumes from the last checkpoint (`attempt` + `resume_from_step`). Pause and take-over use the same mechanism.

### Live view
Runners post events to `z-server`; the API fans them out over SSE (`Last-Event-ID` resume) to the web app and the CLI. Large payloads (diffs, screenshots, terminal output) go to artifacts and events reference them.

## 3. Stack
| Area | Choice |
|---|---|
| Language | Python 3.12, `uv` workspaces (`server`, `runner`, `cli`, `packages/z-common`) |
| API | FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SSE |
| Data | Postgres 16 + pgvector (memory and skill search). No Redis in v1 |
| Queue | Postgres `SELECT … FOR UPDATE SKIP LOCKED`, LISTEN/NOTIFY for wake-ups |
| Agent harness | OpenHands Software Agent SDK behind `AgentBackend` |
| Model gateway | LiteLLM proxy (aliases, virtual keys, budgets, spend logs, fallbacks) |
| Tools | MCP (Python SDK) for the gateway; OpenAPI and GraphQL importers; Playwright for browser connectors |
| CLI | Typer for subcommands, Textual for the interactive session and `z top`, Rich for output |
| Web | React 19, Vite, TypeScript, TanStack Router + Query + Virtual, Tailwind 4, own primitives (no component library), cmdk, `diff` (jsdiff) for diffs, react-markdown, generated API client (`openapi-typescript` + `openapi-fetch`) |
| Observability | Prometheus `/metrics`; plain-text logs (`%(asctime)s %(levelname)s %(name)s %(message)s`, level from `Z_LOG_LEVEL`) with secrets redacted; mission timelines rebuilt from events. Not built yet: OpenTelemetry traces, JSON logs, optional Langfuse export (the runner strips the SDK's `OTEL_*` and Laminar variables so nothing is exported) |
| Packaging | Multi-arch images (amd64, arm64) on GHCR, Helm chart, Compose file |

## 4. Decisions
| ADR | Decision |
|---|---|
| [0001](adr/0001-openhands-sdk-runtime.md) | OpenHands Software Agent SDK as the agent harness, behind `AgentBackend` |
| [0002](adr/0002-litellm-single-egress.md) | LiteLLM is the only path to any model |
| [0003](adr/0003-tool-gateway.md) | A tool gateway owns credentials and policy; agents hold none |
| [0004](adr/0004-postgres-queue.md) | Postgres queue and checkpoints in v1; Temporal or Hatchet later if needed |
| [0005](adr/0005-propose-action.md) | Writes are proposals, applied by a separate executor after approval |
| [0006](adr/0006-agentskills-format.md) | Skills use the AgentSkills `SKILL.md` format |
| [0007](adr/0007-one-chart-three-targets.md) | One Helm chart for k3s and Kubernetes, one Compose file for Docker |
| [0008](adr/0008-textual-cli.md) | Textual for the interactive CLI and `z top` |
| [0009](adr/0009-learning-needs-approval.md) | The learning loop proposes; humans apply |
| [0010](adr/0010-gitlab-oidc-identity.md) | GitLab is the identity provider (OIDC); roles come from GitLab groups |

## 5. What we reuse from the state of the art
| Source | What Z takes |
|---|---|
| Claude Code | The CLI feel: inline session, slash commands, @ and # mentions, permission prompts, plan mode, resume, headless `-p` |
| GitHub Agent HQ / Cursor background agents / Factory | Many agents in parallel, fan-out over repos, one place to watch and decide |
| Devin | Session replay, agents you assign like a colleague |
| Linear | Dense, fast, keyboard-first UI; ⌘K palette; agents as assignees |
| Vercel / GitHub Actions | Run detail with live logs; job grid by status |
| Langfuse / LangSmith | Step timeline with tokens and latency per step |
| Tines / Torq | Human approval steps, safe automation for security teams |
| Composio / Arcade / n8n | Tool auth handled by the platform; connectors from OpenAPI |
| OpenHands SDK | Agent loop, tools, MCP, skills, workspaces, security analyzer |
| kubernetes-sigs/agent-sandbox | Sandbox CRD (gVisor/Kata) as the phase-2 isolation target |
