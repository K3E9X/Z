# Roadmap

Status values: `todo`, `doing`, `done`. Claude Code updates this file at the end of each workstream.

Build order (decided with the user): base first, then one verified slice at a time, each run against the real systems.

## v1.0 — operational (BUILD P0)
| Item | Workstream | Status |
|---|---|---|
| Contracts frozen (`contracts-v1`) | A | done |
| Missions, plans, fan-out, scheduler, resume | B1 | done (draft-plan with the planner model, sessions, save-as-playbook; take-over as a web editor over the checkpointed workspace with restart from the edited checkpoint, ADR 0019) |
| Runner with OpenHands backend, guardrails, code and document tools | B2 | done (real mission on compose through LiteLLM; hardened container via z-socket-proxy) |
| Tool gateway, MCP/OpenAPI/REST/web connectors, exposure review, executor with git helper | B3 | doing (gateway MCP endpoint, REST/OpenAPI/MCP connectors, Jira Cloud and GitLab executors with idempotency, git helper, personal OAuth connections and app-only OAuth, channels Slack/Mattermost/Teams/email, browser connectors with reviewed flows replayed by z-browser and recording by demonstration (ADR 0016), GraphQL and read-only PostgreSQL connectors (ADR 0020), cli connectors in tool containers (ADR 0025), GraphQL import by introspection, rest drafts from API docs, Mattermost and Slack tool connectors (ADR 0026) done; remaining catalog templates of docs/05 §7 are added when a team needs them) |
| CLI: subcommands, headless, interactive session | B4 | doing (headless, interactive session, saved sessions `z -c`, `z --resume`, `z --session` done; `z skills rollback` done; `z skills diff`, `z playbooks save-from` and `z connectors import|record-browser` of docs/10 §3 not built, though their API routes exist) |
| Web: Mission control, Agent, New mission, Review, Connectors, Inference, History, Settings | B5 | done (every screen of docs/09, plus admin, take-over, browser recorder, notifications) |
| Judge, routing policy (key allowlists), evals, memory | B6 | done (pattern + raise-only model judge, pre-action checks, routing policy and rules, signals derived from mission events and action edits (see v1.1 below for the kinds never emitted), eval runs on real missions, learner proposals with eval gates, shadow replay, apply/try, memory retrieval in job specs) |
| Packs core, vuln, ticketing, dev, archi-isp with evals | B7 | done (seeded and lint-clean; every seed agent has ≥ 10 eval cases and every skill ≥ 5 (docs/08 §6), with injection, missing-fact and write-scope cases; the linter enforces rooted check paths and warns below the minimums; not yet run against the real inferX models) |
| Inference monitor (inferX health on the Inference screen) | B1 | done (LiteLLM health, model info, Prometheus/DCGM queries, Inference screen; inferX catalogue wired in ADR 0024; the inferX key set or rotated from the Inference screen, ADR 0027) |
| Named agents (rename, handle, avatar, duplicate) | B1, B5 | done |
| GitLab OIDC sign-in, sessions, step-up, deprovisioning | B1 | done (tested with the fake provider; real GitLab pending) |
| RBAC (roles, scoped bindings, group mapping, separation of duties) | B1 | done (incl. approval rules: four-eyes, two approvers for destructive) |
| Admin area (web + `z admin`) | B1, B4, B5 | done |
| Audit log (append-only, hash chain, signed checkpoints, export, forwarding) | B1 | done (scheduled signed checkpoints and at-least-once syslog/TLS and HTTPS forwarding with delivery status, ADR 0021) |
| CI: GitHub Actions + GitLab CI (scans, signed images, SBOM, k3s deploy) | B8 | done for guard, lint, tests, security scans, images (build, scan, sign, SBOM), chart policy, compose e2e, GitLab staging/production deploy (ADR 0018), license gate on installed packages, kind smoke install under PSS restricted, release notes from conventional commits, ZAP baseline in the e2e job (passive and unauthenticated, web app only, not the API) |
| Compose, Helm (k3s + Kubernetes), images | B8 | doing (compose with z-ui, Helm chart linted and kubeconform-valid for k3s and k8s, kubernetes executor, egress proxy (squid allowlist), z-browser image + compose profile + Helm Deployment/NetworkPolicy done) |
| E2E and acceptance | C | doing (compose e2e: 2 tests, a real mission through the hardened runner, LiteLLM and the fake LLM, and the inferX key set from Z reaching the gateway, plus a ZAP baseline; kind smoke install; most phase C scenarios of BUILD §4 are covered by in-process server tests, not on Compose; no `ACCEPTANCE.md` yet; acceptance against the real inferX, GitLab and Jira pending) |

## v1.1 — complete (BUILD P1)
| Item | Status |
|---|---|
| Learning loop: signals, proposals, shadow replay, apply/rollback | doing (ADR 0014; proposals, shadow replay and apply done; signals `action_edited`, `action_rejected`, `question_answered`, `repair`, `failure`, `guardrail_hit`, `connector_error`, `retry` (a person retried a job) and `feedback` (a mission cancelled with a written reason) emitted; `mission_reassigned` and thumbs/comment feedback not built; rollback exists for skills (`POST /skills/{id}/rollback`) and memory facts (deactivate/restore on `/memory/{id}`), not for agents or playbooks) |
| Connector kinds GraphQL, CLI, database, browser, webhook; connector builder | done (GraphQL, PostgreSQL, browser with recording, cli tools in one-shot sandbox containers (ADR 0025), connector builder and inbound webhooks) |
| `z top`, sessions, `z mcp serve`, themes | done (`/mcp` on z-server and `z mcp serve` share one tool table, ADR 0022) |
| Tasks, Playbooks, Agents and skills, Learning, Mobile screens | done |
| Trace replay, Evals, Analytics screens | done |
| Agent builder | done (`POST /agents/draft` and the web builder) |
| `workplace` pack | done (assistant and researcher agents, inbox triage and weekly report; m365 backed by a shipped Graph v1.0 subset spec, google and gchat backed by subsets checked against Google's discovery documents, optional mattermost and slack connectors; works on Microsoft 365 or Google Workspace) |
| Objectives and assignments (proactive agents) | done (API, z and connector metrics, suggestions in Review, read-only auto-start within budget, `report_objective` endpoint, Objectives screen) |
| Microsoft 365 (Outlook, Teams, SharePoint, OneDrive), Google Workspace, Slack, Mattermost connectors | done (Microsoft 365 mail, calendar, chats, transcripts and file metadata; small file uploads to OneDrive/SharePoint with raw text bodies; Mattermost and Slack channels, threads, posts and replies with crash-safe posting (ADR 0026); Google Workspace Gmail, Calendar, Drive (read, Docs and Sheets as text) and Chat on delegated grants with crash-safe mails, events and posts (ADR 0028); Google service accounts and Docs/Sheets edits later) |
| Channels: Teams bot, Slack, Mattermost, inbound email | done (inbound, replies, follow-up approval cards linking to Z; ADR 0015, 0017) |
| Personal (delegated) connections | done (OAuth PKCE per user, read and write grants apart) |
| Notifications (Mattermost, Slack, email, webhook) | done (Slack, Mattermost, Teams, signed webhooks and SMTP email through a transactional outbox, budget and break-glass alerts, Admin → Notifications, personal email about one's own missions in Settings; ADR 0023) |
| kind/k3d e2e, Grafana dashboard, ExternalSecrets, gVisor how-to | doing (kind smoke install, ExternalSecret template, gVisor how-to in the chart README, Z metrics on /metrics and the "Z overview" Grafana dashboard done; there is no k3d job: e2e runs on Compose and kind only) |

## Known gaps
Not built yet, although the spec describes them:
- Judge classification of staged inputs beyond credentials: a file holding credentials raises the mission to C3; regulated or personal data is not detected.
- Synthetic tool-call probe and `tool_call_parse_error_rate` (the Inference screen shows the field, nothing fills it; `inference.probes` is not read).
- E-mail digests (`notifications.digest`), and answering an agent question by replying to a Z mail (cards link to Z instead).
- Budgets with an objective scope (scopes are org, team, user, playbook and agent).
- Objectives widget on Mission control (the Objectives screen exists).
- Saved searches in the Admin audit log.
- Maintenance mode (no config key, no API).
- Personal connections in the Admin user detail panel.
- The agent builder's "test on a sample mission" step.
- Phase C e2e on Compose: 2 tests against about 18 scenarios in BUILD §4; most are in-process server tests.
- Acceptance (BUILD §4 step 6): `docs/ACCEPTANCE.md` gives the evidence and status of each criterion. Most criteria are partial, and nothing has run against the real inferX, GitLab, Jira or k3s yet.
- ZAP scan: passive, unauthenticated, web app only.
- TUI tests are Textual pilot tests, not snapshot tests.
- Observability: OpenTelemetry traces, JSON logs and Langfuse export (docs/02 §3).
- CLI: `z skills diff` (versions carry no content in the API), `z playbooks save-from`, `z connectors import|record-browser`.

## v2 — later
- Admin-enabled `auto_reversible` autonomy for specific low-risk operations (comments, labels), never destructive, always audited.
- `agent-sandbox` CRD (gVisor/Kata) instead of plain Jobs.
- Temporal or Hatchet if the Postgres queue becomes the bottleneck.
- A2A protocol for agent-to-agent interop with other platforms.
- Pack registry inside the organisation (signed packs).
- Policy engine (Cedar or OPA) replacing YAML policy rules.
