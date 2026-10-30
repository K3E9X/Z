# 01 — Product

## 1. One sentence
Z lets a team hand real work to many AI agents at once, connected to any tool, while seeing everything they do and deciding every change they make.

## 2. Who uses it
| Persona | Needs |
|---|---|
| **Security engineer / architect** (primary) | Launch triage, reviews and bulk changes across dozens of repos or findings; approve the results fast; trust the audit trail |
| **Developer** | Delegate dependency bumps, fixes and MR reviews; review diffs like a PR |
| **Team lead** | See throughput, token spend and value; build playbooks once and let the team reuse them |
| **Platform admin** | Plug in tools and models, set guardrails, budgets and roles, keep data on-prem |

## 3. Principles
1. **An operator console, not a chatbot.** Many agents, statuses at a glance, decisions in one place.
2. **Tool-agnostic.** Any tool with an API, an MCP server, a CLI or only a web UI can be connected. Z is not tied to GitLab or Jira.
3. **On-prem by default.** All model calls go to the internal cluster. External APIs are opt-in and fenced by data class.
4. **Humans decide writes.** Agents read and propose; people approve; the platform executes.
5. **Visible all the way down.** Plan, steps, tool calls, outputs, reasoning, cost: every agent is inspectable live and replayable later.
6. **Gets better with use.** Every approval, edit and rejection is a signal; improvements are proposed, tested and approved.
7. **Same product everywhere.** Docker Compose, k3s or Kubernetes; web, CLI or API.

## 4. Scope
Z is **general-purpose**. The seed packs are security and engineering because that is the first team, but any task that can be described, given tools and checked can be handed to Z: research, writing documents and decks, data analysis, mail and calendar chores, ticket hygiene, documentation, code, reviews, reporting.

### In scope
- Missions: one request, planned into steps, run by one agent or a team (orchestrator + specialist agents + sub-agents), with fan-out over many inputs (repos, findings, tickets, documents, mails, rows of a spreadsheet).
- **Agents you name and shape yourself**: each agent has a name you choose (e.g. "Atlas", "Nova"), a handle for @mentions (`@atlas`), an avatar, a role, instructions and a persona, plus a versioned profile: model, skills, tools, memory, autonomy, evals. Create one from a template, from scratch, by duplicating another, or by describing it in plain language to the agent builder.
- **Objectives and assignments**: give an agent standing objectives ("critical findings triaged within 48 h", "no dependency older than 90 days in `infra/*`", "inbox zero by Friday") and the objects it is responsible for (repos, Jira projects, products, a mailbox, a Teams channel). On its cadence the agent measures the objective, explains the gap and proposes the missions to close it, or starts read-only ones itself when allowed. Writes still need approval.
- Skills in the AgentSkills format (`SKILL.md`), versioned, testable, shareable.
- Playbooks: reusable missions with a team, steps, inputs, trigger, writes and guardrails. Any successful mission can be saved as one.
- Connectors to any tool: MCP, OpenAPI/REST, GraphQL, CLI, read-only database, browser session, inbound webhook. Health, latency, token expiry, exposed operations. A catalog covers Microsoft 365 (Outlook mail and calendar, Teams, SharePoint, OneDrive), Google Workspace, Slack, Mattermost, Atlassian, GitLab, GitHub, ITSM, CI/CD, Kubernetes, cloud consoles, object storage, databases, internal tools (e.g. Logos) and the web (allowlisted). Credentials can be a **service account** or **the user's own account** (OAuth, delegated), so an agent can work on *your* mailbox or calendar for missions you start.
- **Channels**: use Z where people already are. Mention `@Z` or an agent (`@atlas`) in Teams, Slack or Mattermost to start a mission; get progress and approval cards there; forward an email to Z to create a task; get digests by mail. Destructive approvals always go back to the Z app.
- **Work products**: agents produce real files (Markdown, Word, PowerPoint, Excel, PDF, CSV, diagrams, code) as artifacts you can open, download or send through an approved action.
- **Code interpreter**: a sandboxed Python workspace for data analysis, charts and file conversion.
- Intake: Jira label, GitLab assignee, inbound webhooks (e.g. Logos), mail to a Z address, chat mentions, schedules.
- Review inbox: approve, edit or reject writes and answer agent questions; batch approval; mobile.
- Learning loop: signals → proposals (skill edits, memory facts, routing rules) → evals and shadow replay → human approval → versioned apply.
- Inference and usage: inferX health (models, replicas, queue, latency, GPUs), tokens by model/agent/mission/team, budgets and alerts, optional external providers with a routing policy.
- History and audit: every event, signed, exportable as JSON or syslog; mission replay.
- CLI like Claude Code (`z`), live dashboard (`z top`), headless mode, MCP server (`z mcp serve`).
- **Multi-user**: sign in with GitLab (OIDC), roles and teams from GitLab groups, deny-by-default RBAC with separation of duties, an Admin area (users, roles, group mapping, sessions, tokens, audit), and a tamper-evident audit log. Per-team budgets.
- Notifications: Mattermost, Slack, email, webhooks.
- Analytics: hours saved, approval and edit rates, failures by connector, cost.

### Out of scope
- **SIEM, SOC alert handling, DFIR and incident response.** The team already has a tool for this. No SIEM connector pack, no alert-qualification playbook, no forensic agent. (Z still exports its *own* audit log.)
- Multi-tenant SaaS, billing, a public marketplace.
- Training or fine-tuning models. Z improves agents through skills, memory and routing, not weights.
- Autonomous merges or deployments.

## 5. Seed packs
| Pack | Agents | Playbooks |
|---|---|---|
| `dev` | Dev Engineer, Reviewer | Bulk dependency upgrade, Fix an issue, Security review of merge requests |
| `archi-isp` | Security Architect | Project security pre-review, Flow matrix check, EBIOS RM pre-fill (workshops 1–2) |
| `vuln` | Vuln Analyst | Weekly vulnerability triage, Remediate a dependency finding |
| `ticketing` | Ticket Manager | Create tickets from findings (dedup, owner, deadline), Jira backlog grooming, Inactive account review (with Browser Agent) |
| `workplace` | Assistant (mail, calendar, Teams), Writer, Analyst, Researcher | Inbox triage and draft replies, Meeting prep and follow-up from a Teams meeting, Weekly report from Jira + GitLab to Word/PowerPoint, Analyse a spreadsheet and chart it, Research a topic from internal docs and the allowlisted web |
| core | Orchestrator, Browser Agent, Agent Builder | — |

Agent names in the seeds are roles; each team renames its agents (display name, handle, avatar) without changing what they do.

## 6. What "operational" means (v1 acceptance)
1. From the web app and from `z`, start the weekly triage playbook on 50 findings: 8 sub-agents in parallel, live statuses and tokens, ≥ 95 % valid structured outputs, tickets proposed, approved in Review, created once in Jira.
2. Start a bulk dependency upgrade on 5 GitLab repos: 5 diffs with tests run in the sandbox, 5 MRs opened after approval.
3. Run a project security pre-review on a real project file: a usable pre-review document (Markdown and Word).
4. Connect an internal tool from its OpenAPI spec in under 10 minutes and use it in a mission.
5. A prompt-injection string inside a ticket is flagged by the judge and does not change the agent's behaviour; a `curl` from the sandbox is blocked; both appear in History.
6. The Inference screen shows live inferX health and token use by agent and mission.
7. (v1.1) A rejected/edited ticket pattern produces a learning proposal with before/after evals; applying it creates a new skill version that can be rolled back. In v1.0: skill versions and rollback work by hand.
8. The same Helm chart installs on k3s and on Kubernetes; the same compose file runs locally.
9. A team creates its own agent named "Atlas" (from a template, by duplicating, or with the agent builder in v1.1), mentions `@atlas` in the web app and the CLI, and it runs with its own skills and tools.
10. Colleagues sign in with their GitLab accounts; their GitLab groups decide what they can do; an admin changes a mapping in the Admin area with a dry run; every action appears in the audit log and the chain verifies.
11. (v1.1) Connected to Microsoft 365: `@atlas` in a Teams channel starts a mission, the approval card appears in Teams, and the reply is posted after approval; an agent drafts replies for the user's own Outlook inbox, sent only after approval.
