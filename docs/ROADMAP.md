# Roadmap

Status values: `todo`, `doing`, `done`. Claude Code updates this file at the end of each workstream.

Build order (decided with the user): base first, then one verified slice at a time, each run against the real systems.

## v1.0 — operational (BUILD P0)
| Item | Workstream | Status |
|---|---|---|
| Contracts frozen (`contracts-v1`) | A | done |
| Missions, plans, fan-out, scheduler, resume | B1 | todo |
| Runner with OpenHands backend, guardrails, code and document tools | B2 | todo |
| Tool gateway, MCP/OpenAPI/REST/web connectors, exposure review, executor with git helper | B3 | todo |
| CLI: subcommands, headless, interactive session | B4 | todo |
| Web: Mission control, Agent, New mission, Review, Connectors, Inference, History, Settings | B5 | todo |
| Judge, routing policy (key allowlists), evals, memory | B6 | todo |
| Packs core, vuln, ticketing, dev, archi-isp with evals | B7 | todo |
| Inference monitor (inferX health on the Inference screen) | B1 | todo |
| Named agents (rename, handle, avatar, duplicate) | B1, B5 | todo |
| GitLab OIDC sign-in, sessions, step-up, deprovisioning | B1 | done (tested with the fake provider; real GitLab pending) |
| RBAC (roles, scoped bindings, group mapping, separation of duties) | B1 | done (action approval rules come with Review) |
| Admin area (web + `z admin`) | B1, B4, B5 | doing (API done; web and CLI pending) |
| Audit log (append-only, hash chain, signed checkpoints, export, forwarding) | B1 | doing (forwarding and the hourly checkpoint worker pending) |
| CI: GitHub Actions + GitLab CI (scans, signed images, SBOM, k3s deploy) | B8 | doing (python job, supply-chain checks) |
| Compose, Helm (k3s + Kubernetes), images | B8 | doing (compose base + z-server image) |
| E2E and acceptance | C | todo |

## v1.1 — complete (BUILD P1)
| Item | Status |
|---|---|
| Learning loop: signals, proposals, shadow replay, apply/rollback | todo |
| Connector kinds GraphQL, CLI, database, browser, webhook; connector builder | todo |
| `z top`, sessions, `z mcp serve`, themes | todo |
| Tasks, Playbooks, Agents and skills, Learning, Mobile screens | todo |
| Trace replay, Evals, Analytics screens | todo |
| Agent builder | todo |
| `workplace` pack | todo |
| Objectives and assignments (proactive agents) | todo |
| Microsoft 365 (Outlook, Teams, SharePoint, OneDrive), Google Workspace, Slack, Mattermost connectors | todo |
| Channels: Teams bot, Slack, Mattermost, inbound email | todo |
| Personal (delegated) connections | todo |
| Notifications (Mattermost, Slack, email, webhook) | todo |
| kind/k3d e2e, Grafana dashboard, ExternalSecrets, gVisor how-to | todo |

## v2 — later
- Admin-enabled `auto_reversible` autonomy for specific low-risk operations (comments, labels), never destructive, always audited.
- `agent-sandbox` CRD (gVisor/Kata) instead of plain Jobs.
- Temporal or Hatchet if the Postgres queue becomes the bottleneck.
- A2A protocol for agent-to-agent interop with other platforms.
- Pack registry inside the organisation (signed packs).
- Policy engine (Cedar or OPA) replacing YAML policy rules.
