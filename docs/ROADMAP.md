# Roadmap

Status values: `todo`, `doing`, `done`. Claude Code updates this file at the end of each workstream.

Build order (decided with the user): base first, then one verified slice at a time, each run against the real systems.

## v1.0 — operational (BUILD P0)
| Item | Workstream | Status |
|---|---|---|
| Contracts frozen (`contracts-v1`) | A | done |
| Missions, plans, fan-out, scheduler, resume | B1 | done (draft-plan with the planner model, sessions, save-as-playbook; take-over as a web editor over the checkpointed workspace with restart from the edited checkpoint, ADR 0019) |
| Runner with OpenHands backend, guardrails, code and document tools | B2 | done (real mission on compose through LiteLLM; hardened container via z-socket-proxy) |
| Tool gateway, MCP/OpenAPI/REST/web connectors, exposure review, executor with git helper | B3 | doing (gateway MCP endpoint, REST/OpenAPI/MCP connectors, Jira Cloud and GitLab executors with idempotency, git helper, personal OAuth connections and app-only OAuth, channels Slack/Mattermost/Teams/email, browser connectors with reviewed flows replayed by z-browser (ADR 0016) done; interactive flow recording later) |
| CLI: subcommands, headless, interactive session | B4 | done (saved sessions `z -c`, `z --resume`, `z --session`) |
| Web: Mission control, Agent, New mission, Review, Connectors, Inference, History, Settings | B5 | doing (sub-agent) |
| Judge, routing policy (key allowlists), evals, memory | B6 | doing (pattern + raise-only model judge, pre-action checks, routing policy and rules, signals, eval runs on real missions, learner proposals with eval gates and apply/try done; shadow replay, agent builder and memory retrieval in job specs pending) |
| Packs core, vuln, ticketing, dev, archi-isp with evals | B7 | doing (seeded and lint-clean; evals to complete) |
| Inference monitor (inferX health on the Inference screen) | B1 | done (API: LiteLLM health, model info, Prometheus/DCGM queries; web pending) |
| Named agents (rename, handle, avatar, duplicate) | B1, B5 | doing (API done; web pending) |
| GitLab OIDC sign-in, sessions, step-up, deprovisioning | B1 | done (tested with the fake provider; real GitLab pending) |
| RBAC (roles, scoped bindings, group mapping, separation of duties) | B1 | done (incl. approval rules: four-eyes, two approvers for destructive) |
| Admin area (web + `z admin`) | B1, B4, B5 | doing (API done; web and CLI pending) |
| Audit log (append-only, hash chain, signed checkpoints, export, forwarding) | B1 | doing (forwarding and the hourly checkpoint worker pending) |
| CI: GitHub Actions + GitLab CI (scans, signed images, SBOM, k3s deploy) | B8 | done for guard, lint, tests, security scans, images (build, scan, sign, SBOM), chart policy, compose e2e, GitLab staging/production deploy (ADR 0018); ZAP, kind/k3d smoke, license check and changelog later |
| Compose, Helm (k3s + Kubernetes), images | B8 | doing (compose with z-ui, Helm chart linted and kubeconform-valid for k3s and k8s, kubernetes executor, egress proxy (squid allowlist), z-browser image + compose profile + Helm Deployment/NetworkPolicy done) |
| E2E and acceptance | C | todo |

## v1.1 — complete (BUILD P1)
| Item | Status |
|---|---|
| Learning loop: signals, proposals, shadow replay, apply/rollback | doing (all but shadow replay; see ADR 0014) |
| Connector kinds GraphQL, CLI, database, browser, webhook; connector builder | todo |
| `z top`, sessions, `z mcp serve`, themes | doing (`z top`, sessions and themes done; `z mcp serve` pending) |
| Tasks, Playbooks, Agents and skills, Learning, Mobile screens | todo |
| Trace replay, Evals, Analytics screens | todo |
| Agent builder | todo |
| `workplace` pack | todo |
| Objectives and assignments (proactive agents) | done (API, z and connector metrics, suggestions in Review, read-only auto-start within budget, `report_objective` endpoint; web tab pending) |
| Microsoft 365 (Outlook, Teams, SharePoint, OneDrive), Google Workspace, Slack, Mattermost connectors | todo |
| Channels: Teams bot, Slack, Mattermost, inbound email | done (inbound, replies; approval cards pending; ADR 0015) |
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
