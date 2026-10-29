# Roadmap

Status values: `todo`, `doing`, `done`. Claude Code updates this file at the end of each workstream.

Build order (decided with the user): base first, then one verified slice at a time, each run against the real systems.

## v1.0 — operational (BUILD P0)
| Item | Workstream | Status |
|---|---|---|
| Contracts frozen (`contracts-v1`) | A | done |
| Missions, plans, fan-out, scheduler, resume | B1 | done (draft-plan with the planner model, sessions, save-as-playbook; take-over as a web editor over the checkpointed workspace with restart from the edited checkpoint, ADR 0019) |
| Runner with OpenHands backend, guardrails, code and document tools | B2 | done (real mission on compose through LiteLLM; hardened container via z-socket-proxy) |
| Tool gateway, MCP/OpenAPI/REST/web connectors, exposure review, executor with git helper | B3 | doing (gateway MCP endpoint, REST/OpenAPI/MCP connectors, Jira Cloud and GitLab executors with idempotency, git helper, personal OAuth connections and app-only OAuth, channels Slack/Mattermost/Teams/email, browser connectors with reviewed flows replayed by z-browser and recording by demonstration (ADR 0016), GraphQL and read-only PostgreSQL connectors (ADR 0020), cli connectors in tool containers (ADR 0025), GraphQL import by introspection done) |
| CLI: subcommands, headless, interactive session | B4 | done (saved sessions `z -c`, `z --resume`, `z --session`) |
| Web: Mission control, Agent, New mission, Review, Connectors, Inference, History, Settings | B5 | done (every screen of docs/09, plus admin, take-over, browser recorder, notifications) |
| Judge, routing policy (key allowlists), evals, memory | B6 | done (pattern + raise-only model judge, pre-action checks, routing policy and rules, signals, eval runs on real missions, learner proposals with eval gates, shadow replay, apply/try, memory retrieval in job specs) |
| Packs core, vuln, ticketing, dev, archi-isp with evals | B7 | doing (seeded and lint-clean; evals to complete) |
| Inference monitor (inferX health on the Inference screen) | B1 | done (LiteLLM health, model info, Prometheus/DCGM queries, Inference screen; inferX catalogue wired in ADR 0024) |
| Named agents (rename, handle, avatar, duplicate) | B1, B5 | done |
| GitLab OIDC sign-in, sessions, step-up, deprovisioning | B1 | done (tested with the fake provider; real GitLab pending) |
| RBAC (roles, scoped bindings, group mapping, separation of duties) | B1 | done (incl. approval rules: four-eyes, two approvers for destructive) |
| Admin area (web + `z admin`) | B1, B4, B5 | done |
| Audit log (append-only, hash chain, signed checkpoints, export, forwarding) | B1 | done (scheduled signed checkpoints and at-least-once syslog/TLS and HTTPS forwarding with delivery status, ADR 0021) |
| CI: GitHub Actions + GitLab CI (scans, signed images, SBOM, k3s deploy) | B8 | done for guard, lint, tests, security scans, images (build, scan, sign, SBOM), chart policy, compose e2e, GitLab staging/production deploy (ADR 0018), license gate on installed packages, kind smoke install under PSS restricted, release notes from conventional commits, ZAP baseline in the e2e job |
| Compose, Helm (k3s + Kubernetes), images | B8 | doing (compose with z-ui, Helm chart linted and kubeconform-valid for k3s and k8s, kubernetes executor, egress proxy (squid allowlist), z-browser image + compose profile + Helm Deployment/NetworkPolicy done) |
| E2E and acceptance | C | doing (compose e2e: a real mission through the hardened runner, LiteLLM and the fake LLM, plus a ZAP baseline; kind smoke install; acceptance against the real inferX, GitLab and Jira pending) |

## v1.1 — complete (BUILD P1)
| Item | Status |
|---|---|
| Learning loop: signals, proposals, shadow replay, apply/rollback | done (ADR 0014) |
| Connector kinds GraphQL, CLI, database, browser, webhook; connector builder | done (GraphQL, PostgreSQL, browser with recording, cli tools in one-shot sandbox containers (ADR 0025), connector builder and inbound webhooks) |
| `z top`, sessions, `z mcp serve`, themes | done (`/mcp` on z-server and `z mcp serve` share one tool table, ADR 0022) |
| Tasks, Playbooks, Agents and skills, Learning, Mobile screens | done |
| Trace replay, Evals, Analytics screens | done |
| Agent builder | done (`POST /agents/draft` and the web builder) |
| `workplace` pack | done (assistant and researcher agents, inbox triage and weekly report; m365 backed by a shipped Graph v1.0 subset spec, optional mattermost connector) |
| Objectives and assignments (proactive agents) | done (API, z and connector metrics, suggestions in Review, read-only auto-start within budget, `report_objective` endpoint, Objectives screen) |
| Microsoft 365 (Outlook, Teams, SharePoint, OneDrive), Google Workspace, Slack, Mattermost connectors | doing (Microsoft 365 mail, calendar, chats, transcripts and file metadata; small file uploads to OneDrive/SharePoint with raw text bodies; Mattermost channels, threads, posts and replies with crash-safe posting; Google Workspace and Slack as tool connectors later) |
| Channels: Teams bot, Slack, Mattermost, inbound email | done (inbound, replies, follow-up approval cards linking to Z; ADR 0015, 0017) |
| Personal (delegated) connections | done (OAuth PKCE per user, read and write grants apart) |
| Notifications (Mattermost, Slack, email, webhook) | done (Slack, Mattermost, Teams, signed webhooks and SMTP email through a transactional outbox, budget and break-glass alerts, Admin → Notifications, personal email about one's own missions in Settings; ADR 0023) |
| kind/k3d e2e, Grafana dashboard, ExternalSecrets, gVisor how-to | done (kind smoke install, ExternalSecret template, gVisor how-to in the chart README, Z metrics on /metrics and the "Z overview" Grafana dashboard done) |

## v2 — later
- Admin-enabled `auto_reversible` autonomy for specific low-risk operations (comments, labels), never destructive, always audited.
- `agent-sandbox` CRD (gVisor/Kata) instead of plain Jobs.
- Temporal or Hatchet if the Postgres queue becomes the bottleneck.
- A2A protocol for agent-to-agent interop with other platforms.
- Pack registry inside the organisation (signed packs).
- Policy engine (Cedar or OPA) replacing YAML policy rules.
