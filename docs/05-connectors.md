# 05 — Connectors: plugging in any tool

Z is tool-agnostic. A connector describes how to reach a tool; the **tool gateway** decides who may call what, injects credentials and records everything.

## 1. Connector kinds
| Kind | Use it when | How Z builds operations |
|---|---|---|
| `mcp` | The tool has an MCP server (e.g. GitLab, Atlassian) | Gateway runs or connects to the server (stdio sidecar or streamable HTTP) and re-exposes its tools under canonical ids via `operation_map`, filtered. MCP `readOnlyHint`/`destructiveHint` annotations are used as hints, never trusted alone |
| `openapi` | The tool has a REST API with a spec (e.g. Logos, DefectDojo) | Import the spec; one operation per method+path, id from `operationId` or the path (03 §4) |
| `rest` | REST without a spec | Operations declared by hand in YAML, or drafted by the connector builder from docs |
| `graphql` | GraphQL API | Introspection; one operation per allowed query/mutation |
| `cli` | A command-line tool | Each allowed subcommand becomes an operation with a typed argument schema (an argv template, no shell, arguments never become flags); runs in a one-shot tool container of the executor with the credential in its environment (ADR 0025) |
| `database` | Read-only SQL access | Parameterised, read-only queries declared in YAML; never free SQL from the agent |
| `browser` | The tool only has a web UI | A recorded, replayable session (Playwright) run by the gateway-side `z-browser` worker; operations are named flows ("export rules", "list inactive accounts") |
| `builtin` | Platform-provided access (e.g. `web` search/fetch through the egress proxy, C0 only) | Shipped with Z, configured by an admin |
| `webhook` | The tool pushes events to Z | Inbound endpoint with HMAC check; creates tasks or missions |

## 2. The tool gateway
Each job gets one MCP endpoint: `/gateway/mcp?job=<id>`, authenticated by the job token. For every call the gateway:
1. checks the operation is in the job's allowlist and is of class `read` (writes only via the executor);
2. resolves the read credential from the secrets backend and injects it;
3. applies rate limits per connector and per mission;
4. calls the tool with a timeout and retries for idempotent reads;
5. redacts secrets and configured patterns from the response;
6. runs the judge injection scan on the response;
7. truncates large responses into an artifact and returns a summary + reference;
8. emits `tool_call` / `tool_result` events and an audit record.

The **action executor** uses the same gateway code path with the write credential, after approval, with an idempotency key per action.

## 3. Operation classes and exposure
- `read`: may be exposed to agents.
- `write`: never callable directly by agents; can be the target of `propose_action`.
- `destructive` (delete, disable, revoke, merge, deploy): not exposed by default. An admin can expose it to `propose_action`; it then always needs two approvers.
- **Classifier (platform floor)**: `GET/HEAD` (or MCP tools with a read-only hint and a read-like name) → read; everything else → write; `DELETE` and names matching `delete|remove|purge|revoke|destroy|disable|deactivate|merge|deploy|approve|transfer` → destructive. A connector rule may **raise** a class, never lower it. Globs in rules apply only to operations the classifier marks read.
- **Exposure review**: imported or pack-shipped connectors start `pending_review`. Pack `exposed: true` values are pre-ticked proposals; an admin confirms each operation (`POST /connectors/{id}/approve-exposure`) before any agent can see it.

## 4. Credentials
- One credential per purpose: `read` (gateway) and `write` (executor), and one scope: `service` or `user` (see §9).
- Backends: env (dev), file, Kubernetes Secret, HashiCorp Vault (KV v2 and dynamic secrets). Credentials are references in the database, never values.
- Expiry tracking: `expires_at` from the token metadata when available, else from config; `token_expiring` health state 7 days before.
- Rotation: a new version is tested with `connectors test` before it replaces the old one.

## 5. Health
Every connector has a health probe (path, MCP `ping`, or a cheap read). The monitor records status, p50/p95 latency, error rate, last success, and the agents using it. The UI and `z top` show it; a down connector puts jobs that need it in `needs_attention (connector_down)` instead of failing them.

## 6. Connector builder
Adding a tool should take minutes:
1. **From a spec**: paste an OpenAPI/GraphQL URL or file → operations listed with proposed classes → admin reviews exposure → test call → save.
2. **From docs**: the builder agent (`z-heavy`) reads API docs pasted or uploaded, drafts a `rest` connector YAML and tests it; an admin reviews.
3. **From the browser**: the admin writes the flow (steps such as `fill`, `click`, `extract_table`, `assert_text`) for a named, parameterised `browser` operation; the gateway-side `z-browser` worker replays it with the read or write account, limited to the connector's hosts (ADR 0016). The admin can also record it by demonstration: drive a live page from the web app ("Record a flow" on the connector); credentials are typed by Z and recorded as placeholders; the draft is reviewed before exposure.
4. **From an MCP server**: give the command or URL; Z discovers tools and proposes classes.

Every connector created this way is versioned YAML that can live in Git (`packs/*/connectors/`).

## 7. Connector catalog
No tool is out of reach: anything below ships as a template (YAML + operation classes + previews), and anything not listed is added with the builder. **P0** = built in v1.0, **P1** = v1.1, the rest are templates to finish when a team needs them.

| Family | Connectors | Typical read | Typical write (via executor) | Kind |
|---|---|---|---|---|
| Code | **GitLab (P0)**, GitHub, Bitbucket, Gitea | repos, files, MRs/PRs, issues, pipelines | branch push, MR/PR, issue, comment | mcp / openapi |
| Work tracking | **Jira (P0)**, Linear, Azure Boards, GitLab issues | search, issue, sprint | create/update issue, comment, transition | mcp / openapi |
| Docs and knowledge | **Confluence (P0 read)**, SharePoint, Notion, Google Drive, wiki | pages, search, files | page create/update | mcp / openapi |
| **Microsoft 365 (P1)** | **Outlook mail**, **Outlook calendar**, **Teams** (channels, chats, meetings, transcripts), **SharePoint**, **OneDrive**, Planner, To Do | mail search/read, events, free/busy, channel messages, meeting transcripts, files | send/reply mail, create event, post in a channel or chat, upload file, create task | Microsoft Graph (openapi) |
| Google Workspace | Gmail, Calendar, Drive, Docs, Sheets, Chat | same as above | same as above | openapi |
| Chat | **Mattermost (P1)**, Slack, Teams (via Graph) | channel history, threads | post, reply | openapi / mcp |
| Email (generic) | IMAP/SMTP | mailbox search | send | rest (gateway-side client) |
| Vulnerability and security tooling | **DefectDojo (P0)**, scanners (e.g. Logos), dependency trackers, secret scanners | findings, products | — (v1) | openapi |
| Internal tools | **Logos (P0 template)**, any tool with an API, a CLI or only a UI | per tool | per tool | openapi / rest / cli / browser |
| ITSM | ServiceNow, GLPI, Jira Service Management | tickets, CMDB | create/update ticket | openapi |
| CMDB and directory | **CMDB (P0)**, LDAP/Active Directory, Entra ID | asset owner, environment, user status, last logon | — (proposals only, e.g. disable account) | rest / database |
| CI/CD and platforms | GitLab CI, GitHub Actions, Jenkins, Argo CD, Harbor | pipelines, logs, apps, images | re-run pipeline, sync app (destructive class) | openapi |
| Kubernetes and cloud (read) | Kubernetes API (read-only kubeconfig), AWS, Azure, GCP, OpenStack | resources, configs, costs | — (v1) | cli / openapi |
| Observability (read) | Prometheus, Grafana, Loki | queries, dashboards | — | openapi |
| Data | Postgres, MySQL, ClickHouse (read-only views), S3/MinIO, SFTP, spreadsheets | parameterised queries, objects, files | upload object/file | database / rest |
| Web | Web search and fetch through an allowlisted proxy | public pages (C0) | — | built-in |
| Sandbox | **Isolated terminal (P0)**, **code interpreter (P0)** | — | — | built-in |

Out of scope by decision: SIEM, SOC and DFIR tooling (handled by an existing tool).

## 8. Microsoft 365 in detail
- **Two Entra ID app registrations**, so read and write stay apart even for delegated access: `z-m365-read` (read scopes, tokens held by the gateway) and `z-m365-write` (write scopes, tokens held by the executor only).
  - **Delegated (user) connections**: each person connects their own account from *Settings → Connections* and consents to both apps (two OAuth authorization code + PKCE flows, two token sets stored separately). Agents work on that person's mail, calendar and files only in missions that person starts or triggers they own. Refresh of read tokens happens in the gateway, of write tokens in the executor. Revocable by the user.
  - **Application permissions** for shared resources (a team mailbox, a channel, a SharePoint site), restricted with Exchange **RBAC for Applications** (or application access policies) and Teams resource-specific consent so the app only reaches the mailboxes, teams and sites it needs.
- Read scopes (`Mail.Read`, `Calendars.Read`, `Chat.Read`, `Files.Read.All`…) belong to `z-m365-read`; write scopes (`Mail.Send`, `ChannelMessage.Send`, `Calendars.ReadWrite`, `Files.ReadWrite`) to `z-m365-write`. Request the minimum; check Microsoft Graph docs for which Teams read APIs are protected or metered and need extra approval.
- Previews in Review: a rendered mail (from, to, cc, subject, body, attachments), an event card, a Teams message with its target channel, a file with its destination.
- Default data class for M365 content: C2.

## 9. Credential scopes
| Scope | Who it acts as | When it is used |
|---|---|---|
| `service` | A service account shared by the team | Missions on shared resources (repos, projects, team mailbox) |
| `user` | The person who started the mission (or the owner of the trigger that started it), via their delegated grants | Their own mailbox, calendar, files, chats |

A mission records whose identity each call used; the History screen shows it as "runs as". An agent can never use another person's `user` credential.

## 10. Channels: using Z from Teams, Outlook, Slack, Mattermost
| Channel | In | Out |
|---|---|---|
| Teams (Bot Framework, via the Entra app) | `@Z` or `@<agent handle>` in a channel or chat starts a mission; replies in thread guide it | Progress updates in the thread, approval cards (Adaptive Cards) for non-destructive writes, final summary |
| Outlook / email | Mail to a Z address (or forward) creates a task with attachments; reply to a Z mail answers a question | Digests (daily decisions, weekly report), notifications |
| Slack / Mattermost | Mention or slash command `/z` | Thread updates, approval buttons |
- Chat users are mapped to Z users through SSO; unknown users cannot start missions.
- Approving from a chat card requires a fresh Z session check; **destructive actions and two-approver actions can only be approved in the Z app**. In v1 every card links to the action's review in Z (ADR 0017); chat buttons never approve.
- Follow-ups carry content (summaries, questions) only up to the channel's `content_up_to` data class (default C1); above it they say the details are in Z.
- Channel content is untrusted input, scanned by the judge like any tool output.

## 11. Intake (tools pushing work to Z)
| Source | Rule |
|---|---|
| Jira | Issues with label `z` (configurable) in allowed projects → task; optional auto-start with a playbook |
| GitLab | Issues or MRs assigned to the `@z` user, or labelled → task |
| Webhook | `POST /api/v1/webhooks/{trigger_id}` with HMAC → task or mission |
| Mail | Message to the Z address → task (attachments become inputs) |
| Chat | Mention in Teams, Slack, Mattermost → task or mission |
| Schedule | Cron on a playbook |

Tasks land on the Tasks board as *Incoming* with a suggested agent (routing rules + judge).
