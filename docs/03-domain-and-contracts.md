# 03 — Domain and contracts

Phase A of the build turns this document into `contracts/` (OpenAPI 3.1 + JSON Schemas). After the `contracts-v1` tag, contracts win over this text. **YAML keys in §4 are canonical**: the database and API use the same names.

## 1. Entities

| Entity | Purpose | Key fields |
|---|---|---|
| `Team` | Owns agents, playbooks, budgets | id, name |
| `User` | Person, from GitLab OIDC | id, issuer, sub, username, name, email, avatar, teams[], status (`active`/`suspended`/`deprovisioned`), last_login_at, mfa_enforced |
| `Role` | Named set of permissions | id, name, builtin, permissions[] |
| `RoleBinding` | Who has which role where | id, subject (`user:<id>` / `group:<gitlab path>`), role, scope (`org` / `team:<id>`), source (`gitlab`/`manual`), expires_at?, created_by |
| `GroupMapping` | GitLab group → role or team | id, group_path, role?, scope?, team?, source (`config`/`db`) |
| `AuthSession` | Web session or CLI token | id, user, kind (`web`/`cli`), device, ip, user_agent, created_at, last_seen_at, expires_at, step_up_at?, revoked_at? |
| `ApiToken` | Personal token for scripts | id, user, name, scopes[], hash, expires_at, last_used_at, revoked_at? |
| `Pack` | Distribution bundle | name, version, requires_connectors[], optional_connectors[], agents[], skills[], playbooks[], connectors[] |
| `Agent` | Versioned agent profile, named by its team | id, name (stable slug), display_name (free, e.g. "Atlas"), handle (`@atlas`, unique per org), avatar, role, instructions, persona, template, version, model, escalate_model, skills[], tools[], memory, autonomy, limits, evals, owner_team, visibility (`team`/`org`), status (`working`/`available`/`disabled`) |
| `Objective` | A standing goal given to an agent | id, agent, title, scope[] (objects), metric {source, query/operation, params, target, direction}, cadence, allowed_playbooks[], mode (`suggest` / `auto_start_read_only`), budget_per_period {tokens, period}, status (`on_track`/`at_risk`/`off_track`/`paused`), owner_user |
| `Assignment` | Objects an agent looks after | id, agent, object_kind (`repo`/`group`/`jira_project`/`product`/`mailbox`/`channel`/`connector`/`document_space`/`custom`), object_ref, role (`owner`/`watcher`), since |
| `Skill` | `SKILL.md` + resources, versioned | id, name, version, path, origin (`pack`/`learned`/`user`), evals, checksum |
| `Playbook` | Reusable mission | id, name, version, domain, inputs.schema, team[], connectors[], steps[], trigger?, autonomy, four_eyes, budget, usage_count |
| `Mission` | One request | id, title, prompt, playbook_id?, plan, inputs, connectors[], data_class, autonomy, plan_first, status, created_by, session_id?, trigger_id?, budget, tokens_in/out, started/ended |
| `Plan` | Steps of a mission | steps[]: {id, title, agent, fanout?, depends_on[], inputs_from?, when?, writes[], workspace, credentials, status} |
| `Job` | One agent run on one input | id, mission, step, agent@version, input, status, attempt, checkpoint, resume_from_step, tokens, started/ended, error |
| `Event` | Everything that happens | mission, job?, seq, ts, type, payload |
| `Artifact` | Files produced | job, kind, name, path, size, sha256, data_class |
| `ProposedAction` | A write waiting for a human | id, job, connector, operation, class, payload, preview, reversible, judge_verdict, runs_as, status, approvals[], result, idempotency_key |
| `Question` | Agent asks a human | id, job, text, options[], answer, answered_by |
| `Task` | Work that came in, not yet a mission | id, source (`jira`/`gitlab`/`webhook`/`email`/`chat`/`schedule`/`manual`), external_ref, title, body_ref, suggested_agent, suggested_playbook, status (`incoming`/`scheduled`/`started`/`dismissed`), mission_id?, created_at |
| `Session` | A CLI or chat conversation that can start missions | id, user, surface (`cli`/`web`/`teams`/`slack`/`mattermost`), transcript_ref, missions[], created/updated |
| `Connector` | A connected tool | id, name, kind, base_url, auth, health, latency_p95, data_class, operations[], review_status (`pending_review`/`approved`) |
| `Operation` | One callable thing on a connector | id (canonical, see §4), connector, source_ref (MCP tool name or method + path), class (`read`/`write`/`destructive`), exposed, rate_limit, input_schema, reviewed_by |
| `Credential` | Reference to a secret, never the value | id, connector, purpose (`read`/`write`), scope (`service`/`user`), owner_user?, backend, ref, scopes[], rotated_at, expires_at |
| `Channel` | A place people talk to Z | id, kind (`teams`/`slack`/`mattermost`/`email`), config, default_agent, allowed_teams[], identity_mapping |
| `Trigger` | Starts missions or tasks | id, kind (`schedule`/`jira`/`gitlab`/`webhook`/`email`/`chat`), config, filter, playbook, inputs (templated), owner_user, auto_start, enabled |
| `MemoryFact` | Approved knowledge | id, scope (`org`/`team`/`agent`), text, embedding, source, approved_by, version, expires_at? |
| `Signal` | Learning input | id, kind, mission, job, agent, skill, payload, ts |
| `Proposal` | Suggested improvement | id, kind (`skill_edit`/`memory_fact`/`routing_rule`/`playbook_edit`), target, diff, evidence[], eval_before, eval_after, regressions, cost_delta, status |
| `EvalSuite` / `EvalRun` | Tests for skills and agents | cases[] (see `08`); run: target@version, model, scores, passed |
| `Provider` | Model endpoint | id, name, kind (`internal`/`external`), aliases[], data_classes[], enabled |
| `ModelAlias` | What agents ask for | name (`z-heavy`, `z-fast`, `z-judge`, `z-embed`, `ext-*`), provider, context, tool_calling_ok |
| `RoutingRule` | Which agent or playbook for what | match, target, origin (`config`/`learned`), version |
| `Budget` | Token limits | scope (`org`/`team`/`user`/`playbook`/`mission`/`agent`/`objective`), limit, period (`mission`/`day`/`week`/`month`), alert_at |
| `UsageRecord` | Aggregated spend | ts_bucket, model, agent, mission, team, user, tokens_in, tokens_out, cached_tokens, requests |
| `AuditEvent` | Tamper-evident log (see `12` §4) | seq, ts, actor, actor_ip, user_agent, session_id, action, target_type, target_id, team, result, runs_as, before?, after?, request_id, prev_hash, hash |
| `AuditCheckpoint` | Signed chain head | seq, hash, signature, ts |

## 2. Statuses and enums

**Mission**: `draft → planned → queued → running ⇄ waiting (approval | question) → succeeded | failed | cancelled | budget_exceeded | timed_out`. Plus `paused` (user) at any time while running.

**Job**: `queued → running → (needs_attention) → succeeded | failed | cancelled | budget_exceeded | timed_out`. `needs_attention` reasons: `question`, `approval`, `invalid_output`, `loop_detected`, `guardrail_confirm`, `connector_down`, `judge_blocked`.

**ProposedAction**: `pending → approved → applying → applied | failed`, or `rejected`, or `superseded` (edited and re-proposed). `class: destructive` needs `approvals ≥ 2` from different users.

**Proposal** (learning): `observed → proposed → evaluating → ready | discarded → applied | rejected`; `applied → rolled_back`.

**Connector health**: `healthy`, `degraded`, `token_expiring`, `unreachable`, `disabled`.

**Autonomy** (one enum for agents, playbooks and missions): `read_only` (no proposals possible) | `propose` (default: writes become proposals). **The strictest of agent, playbook and mission wins.** `auto_reversible` is reserved for v2.
**Plan first** is separate: a mission boolean `plan_first` (draft the plan and wait for the user before running). The CLI's `Shift+Tab` cycles `propose` → `read_only` → `plan first`.
**Objective mode**: `suggest` (default: proposes missions) | `auto_start_read_only` (may start missions whose plan has no `writes`, within its budget).

## 3. Events
Envelope: `{ mission_id, job_id (null for mission-level events), seq, ts, type, agent?, step_id?, payload }`.

| Type | Payload |
|---|---|
| `status` | entity (`mission`/`job`), from, to, reason |
| `plan` | steps[] (initial or updated) |
| `message` | role, text |
| `thought` | text (Reasoning tab, collapsible) |
| `tool_call` | call_id, tool, operation, args (redacted) |
| `tool_result` | call_id, ok, summary, artifact_ref?, duration_ms, judge (`clean`/`flagged`/`blocked`) |
| `terminal` | command, exit_code, output_ref |
| `file_edit` | path, diff_ref, added, removed |
| `browser` | url, action, screenshot_ref |
| `denied` | what, rule, detail |
| `validation_error` | where (`tool_args`/`output`), errors[] |
| `usage` | model, tokens_in, tokens_out, cached_tokens, latency_ms |
| `artifact` | artifact_id, kind, name |
| `question` | question_id, text, options[] |
| `question_answered` | question_id, answer, answered_by |
| `action_proposed` | action_id, connector, operation, class, summary |
| `action_status` | action_id, from, to, by?, result? |
| `subagent` | child_job_id, agent, input_summary |
| `checkpoint` | step, size |
| `data_class` | from, to, cause (tool result, input, judge) |
| `learning_signal` | signal_id, kind |
| `objective_update` | objective_id, value, target, status |

## 4. Manifests

### Operation ids (canonical)
Every operation has an id `<connector>.<resource>.<verb>` with a **plural resource**: `gitlab.merge_requests.create`, `jira.issues.search`, `cmdb.assets.get`, `m365.calendar.events.create`, `logos.accounts.disable`. Inside a connector file the prefix is omitted (`merge_requests.create`).
- **MCP** connectors map server tool names to ids with `operation_map` (unmapped tools are ignored).
- **OpenAPI** connectors derive ids with `operation_ids: from_operation_id` (slug of `operationId`) or `from_path` (`GET /api/v2/findings/` → `findings.list`, `GET …/{id}/` → `findings.get`, `POST` → `create`, `PUT/PATCH` → `update`, `DELETE` → `delete`, other `POST /x/{id}/<action>` → `<action>`). An `operation_map` can override.
- **REST, GraphQL, CLI, database, browser** connectors declare ids explicitly.
- Agent `tools` and step `writes` refer to ids. `gateway:<connector>.<resource>.*` globs are allowed **only for read operations**; writes are always listed by full id.

### `pack.yaml`
```yaml
name: vuln
version: 1.0.0
description: Vulnerability triage and remediation
owner: cyber-ops
requires_connectors: [defectdojo, cmdb, jira]
optional_connectors: [web, gitlab]
```

### `agents/<name>.yaml`
```yaml
name: vuln-analyst              # stable slug from the template
display_name: Vuln Analyst      # teams rename freely, e.g. "Vega"
handle: vuln-analyst            # @mention handle, unique per org
avatar: { initials: VA, color: "#5DBE8A" }
version: 1.6.0
role: "Qualifies vulnerability findings in their real context and proposes priorities and owners."
instructions: |
  Be factual. For every priority give the evidence.
persona: { tone: concise, language: en }
model: z-fast                   # alias only, never a provider model ID
escalate_model: z-heavy
skills: [triage-finding, environment-context, contextual-cvss]
tools:                          # neutral tool names (04 §1); built-ins are implicit
  - gateway:defectdojo.findings.*
  - gateway:cmdb.assets.*
  - gateway:web.fetch
  - read
memory: { scopes: [org, team, agent], top_k: 8 }
autonomy: propose               # read_only | propose
limits: { max_steps: 60, max_tokens: 600000, timeout_s: 1800 }
evals: evals/vuln-analyst.jsonl # relative to the pack
```
**Built-ins** are implicit for every agent: `ask_human`, `search_memory`, `report_objective`, and `propose_action` (usable only for the `writes` of the current step). `spawn_subagent` must be listed (Orchestrator only by default). An agent never lists write operations.

### `playbooks/<name>.yaml`
```yaml
name: weekly-vuln-triage
version: 1.6.0
domain: vulnerabilities
description: Qualify every critical finding and draft one ticket per owning team.
inputs:
  schema:
    type: object
    required: [product]
    properties:
      product: { type: string }
      min_severity: { type: string, enum: [high, critical], default: critical }
team: [vuln-analyst, ticket-manager]      # every step agent must be in the team
connectors: [defectdojo, cmdb, jira]      # becomes Mission.connectors (scope of gateway:@read)
steps:
  - id: pull
    agent: vuln-analyst
    prompt: List open findings for {{ inputs.product }} with severity >= {{ inputs.min_severity }}.
    output: { schema: schemas/finding-list.json }      # required when another step fans out over it
  - id: qualify
    agent: vuln-analyst
    depends_on: [pull]
    fanout: { over: steps.pull.output.findings, concurrency: 8 }
    prompt: Apply triage-finding to finding {{ item.id }}.
    output: { schema: schemas/triage-result.json, max_repair_attempts: 2 }
  - id: tickets
    agent: ticket-manager
    depends_on: [qualify]
    inputs_from: steps.qualify.outputs   # fan-in: the list of all outputs of a fan-out step
    prompt: Group by owning team, deduplicate, propose one ticket per team.
    writes: [jira.issues.create, jira.comments.create]
trigger:
  kind: schedule                          # schedule | jira | gitlab | webhook | email | chat
  cron: "0 8 * * 1"
  owner_user: alice@example.test          # the identity triggered missions run as (for `user` credentials, budgets, audit)
  inputs: { product: infra-boutiques }    # templated; event triggers use {{ event.* }}
autonomy: propose
four_eyes: false
budget: { max_tokens: 6000000, timeout_s: 7200 }
```
Step keys: `id`, `agent`, `prompt`, `depends_on`, `fanout {over, concurrency, when?}`, `inputs_from`, `when` (expression over earlier outputs), `output {schema, max_repair_attempts}`, `writes[]`, `workspace` (`none` | `git_clone` with `repo` | `files`), `credentials` (`service` default | `user`, allowed only when the mission's starter or the trigger owner is that user), `model` (override).
**Write timing**: `propose_action` is available only in steps that list `writes`. Put review steps *before* the step that proposes (see `packs/dev/playbooks/bulk-dependency-upgrade.yaml`).

### `connectors/<name>.yaml`
```yaml
name: logos
kind: openapi            # mcp | openapi | rest | graphql | cli | database | browser | webhook | builtin
display_name: Logos
base_url: ${LOGOS_URL}
spec: ./logos.openapi.json          # path, URL, or builtin:<name>
operation_ids: from_operation_id    # openapi only: from_operation_id | from_path
operation_map: {}                   # source name -> canonical id (mcp; optional override for openapi)
mcp: { transport: stdio, command: "…" }        # mcp only (or { transport: http, url: … })
declared: []                        # rest/graphql/cli/database/browser: [{id, method, path | query | command | flow, input_schema}]
auth:
  read:  { type: bearer, secret: env:LOGOS_TOKEN_RO }       # bearer | api_key {header, prefix} | basic | oauth (see m365) | mtls
  write: { type: bearer, secret: env:LOGOS_TOKEN_RW }       # executor only
oauth: {}                           # oauth connectors: tenant, read_app, write_app (two apps, see 05 §8)
operations:
  default_exposed: false
  rules:                            # first match wins; globs only for read
    - { match: "assets.get", class: read, exposed: true }
    - { match: "accounts.list", class: read, exposed: true }
    - { match: "accounts.disable", class: destructive, exposed: true }
    - { match: "accounts.delete", class: destructive, exposed: false }
rate_limit: { per_minute: 60 }
health: { operation: assets.get, params: { id: health }, interval_s: 60 }   # or { path: /health }
data_class: C2
```
**Platform floor** (cannot be lowered by any pack): the classifier of `05 §3` marks operations as `destructive` by method and name; a rule may raise a class, never lower it. Pack `exposed: true` values are **proposals**: on install the connector is `pending_review` and an admin confirms exposure per operation before agents see anything.

### Skill (`skills/<name>/SKILL.md`)
AgentSkills format: YAML front matter (`name`, `description`) then instructions. Z adds optional `version`, `evals`, `owner`. Skills may ship `scripts/` (run only in the sandbox) and `references/`.

### Objective (`objectives/*.yaml`)
```yaml
agent: vuln-analyst
title: Critical findings triaged within 48 hours
scope: [{ kind: product, ref: infra-boutiques }]
metric:
  source: z                 # z (built-in Z metrics) | connector (a read operation + a JSONPath count/sum)
  query: untriaged_findings # z metrics: untriaged_findings, open_proposals, stale_tickets, overdue_tickets, mission_failures
  params: { product: infra-boutiques, severity: critical, older_than_hours: 48 }
  target: 0
  direction: down           # down | up
cadence: "0 */4 * * *"      # cron, or { event: <trigger kind> }
allowed_playbooks: [weekly-vuln-triage]
mode: suggest               # suggest | auto_start_read_only
budget_per_period: { tokens: 2000000, period: day }
```

### Actions
Every write is `{connector, operation, payload}` validated against the operation's input schema, plus a human-readable `preview`, `class`, `reversible` and `runs_as`. Built-in previews: `gitlab.merge_requests.create` (diff), `gitlab.issues.create`, `gitlab.notes.create`, `jira.issues.create`, `jira.issues.update`, `jira.comments.create`, `m365.mail.send` / `m365.mail.reply` (rendered mail), `m365.calendar.events.create` (event card), `m365.teams.messages.post` (message + target), `m365.files.upload` (file + destination), `slack.messages.post`, `mattermost.posts.create`; generic JSON for others.
`gitlab.merge_requests.create` payload: `{project, source_branch, target_branch, title, description, patch_artifact}`. The executor's **git helper** clones with the read credential, applies the patch, commits as the Z bot, pushes the branch with the write credential, then creates the MR; all in one idempotent action.

## 5. API (REST + SSE)
All under `/api/v1`. JSON. Cursor pagination. Errors as RFC 9457 problem details. The **owner** column says which BUILD workstream implements the endpoint.

| Area | Endpoints | Owner |
|---|---|---|
| Auth | `GET /auth/login` (redirect to GitLab), `GET /auth/callback`, `POST /auth/logout`, `POST /auth/step-up`, `POST /auth/cli/exchange` (GitLab device/PKCE token → Z CLI token), `GET /me`, `GET /me/sessions`, `DELETE /me/sessions/{id}`, `GET/POST /me/tokens`, `DELETE /me/tokens/{id}` | B1 |
| Missions | `POST /missions` (prompt or playbook + inputs), `POST /missions/draft-plan`, `GET /missions`, `GET /missions/{id}`, `PATCH /missions/{id}/plan`, `POST /missions/{id}/start\|pause\|resume\|cancel`, `POST /missions/{id}/messages`, `POST /missions/{id}/take-over`, `GET /missions/{id}/events` (SSE), `POST /missions/{id}/save-as-playbook` | B1 |
| Jobs | `GET /missions/{id}/jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/events` (SSE), `GET /jobs/{id}/artifacts`, `GET /artifacts/{id}`, `POST /jobs/{id}/retry` | B1 |
| Review | `GET /review`, `POST /actions/{id}/approve\|reject\|edit`, `POST /actions:batch-approve`, `POST /questions/{id}/answer` | B1 (executor hand-off B3) |
| Agents & skills | `GET/POST /agents`, `GET/PATCH /agents/{id}`, `POST /agents/{id}/duplicate`, `GET /agents/{id}/versions`, `POST /agents/draft` (builder), `GET /skills`, `GET /skills/{id}`, `GET /skills/{id}/versions`, `POST /skills/{id}/rollback` | B1 (`draft`: B6) |
| Objectives | `GET/POST /agents/{id}/objectives`, `PATCH /objectives/{id}`, `GET /objectives/{id}/history`, `POST /objectives/{id}/evaluate`, `GET/POST /agents/{id}/assignments` | B1 |
| Playbooks | `GET/POST /playbooks`, `GET/PATCH /playbooks/{id}`, `POST /playbooks/{id}/run` | B1 |
| Tasks & triggers | `GET /tasks`, `PATCH /tasks/{id}` (start, dismiss), `GET/POST /triggers`, `POST /webhooks/{trigger_id}` (HMAC) | B1 |
| Sessions | `GET/POST /sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/messages` | B1 |
| Connectors | `GET/POST /connectors`, `GET/PATCH /connectors/{id}`, `POST /connectors/{id}/approve-exposure`, `POST /connectors/{id}/test`, `POST /connectors/import`, `POST /connectors/record-browser`, `GET /connectors/{id}/operations`, `PATCH /operations/{id}` | B3 |
| Personal connections | `GET /me/connections`, `POST /me/connections/{connector}/oauth/start`, `GET /oauth/callback`, `DELETE /me/connections/{id}` | B3 |
| Channels | `GET/POST /channels`, `POST /channels/teams/messages`, `POST /channels/slack/events`, `POST /channels/mattermost/hook`, `POST /channels/email/inbound` | B3 |
| Inference & usage | `GET /inference/health`, `GET /inference/models`, `GET /inference/nodes`, `GET /usage?group_by=model\|agent\|mission\|team\|user&from&to&bucket`, `GET/PUT /budgets`, `GET/POST /providers`, `GET/PUT /routing-policy` | B1 (policy: B6) |
| Learning & evals | `GET /signals`, `GET /proposals`, `GET /proposals/{id}`, `POST /proposals/{id}/apply\|reject\|try`, `GET/POST /evals`, `GET /evals/{run_id}` | B6 |
| History | `GET /audit`, `GET /audit/export?format=json\|syslog`, `GET /missions/{id}/replay` | B1 |
| Admin (step-up on every write) | `GET /admin/users`, `GET/PATCH /admin/users/{id}` (suspend, reactivate), `POST /admin/users/{id}/revoke-sessions`, `GET/POST/DELETE /admin/role-bindings`, `GET/POST/PATCH /admin/roles`, `GET /admin/permissions`, `GET /admin/who-can?permission=`, `GET/PUT /admin/group-mapping`, `POST /admin/group-mapping:dry-run`, `GET/PUT /admin/auth-settings`, `POST /admin/auth-settings:test`, `GET /admin/sessions`, `DELETE /admin/sessions/{id}`, `GET /admin/tokens`, `DELETE /admin/tokens/{id}`, `GET/POST /teams`, `GET/PATCH /teams/{id}`, `GET/PUT /settings/guardrails`, `GET/PUT /admin/audit-settings` (retention, forwarding), `POST /admin/audit:verify`, `GET /analytics` | B1 |
| Live | `GET /stream` (SSE: review counts, statuses, health) | B1 |
| Internal (runner, job token) | `GET /internal/jobs/{id}/spec`, `POST /internal/jobs/{id}/events`, `POST /internal/jobs/{id}/artifacts`, `POST /internal/jobs/{id}/checkpoint`, `POST /internal/jobs/{id}/actions`, `POST /internal/jobs/{id}/questions`, `POST /internal/jobs/{id}/complete` | B1 |
| MCP | `/mcp` (Z as an MCP server: `start_mission`, `get_mission`, `list_playbooks`, `list_review`) | B1 |
| Gateway | `/gateway/mcp` (per-job tool gateway), internal `POST /internal/jobs/{id}/data-class` (raise class) | B3 |

## 6. Roles
| Role | Can |
|---|---|
| `viewer` | Read missions, history (own events), usage |
| `operator` | Start, pause, guide and cancel missions; answer questions |
| `approver` | Approve or reject actions for connectors in their teams |
| `builder` | Edit agents, skills, playbooks, objectives; apply learning proposals for their team |
| `auditor` | Read and export the full audit log |
| `admin` | Users, roles, group mappings, sessions, tokens, connectors and exposure review, credentials, providers, routing policy, guardrails, budgets, channels |

Full permission matrix, scoping, separation of duties and GitLab group mapping: `12-identity-access-and-admin.md` §2.
