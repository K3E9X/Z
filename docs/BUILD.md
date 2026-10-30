# BUILD — building Z with Claude Code

Strategy: a short sequential phase freezes the contracts, then eight workstreams run in parallel (one sub-agent each, in its own git worktree), then the main session integrates. Work is tagged **P0** (needed for "operational", `01-product.md` §6) or **P1** (complete product). Finish every P0 across all workstreams before starting P1.

---

## 0. Prerequisites (manual)
1. Tools: Docker, `uv`, Node 22+ and `pnpm`, `git`, `helm`, `kubectl`, `kind` or `k3d` for local cluster tests.
2. `cp .env.example .env` and fill it. `cp config/litellm.example.yaml config/litellm.yaml` with the real inferX model IDs. `cp config/z.example.yaml config/z.yaml`.
3. Check every model before mapping it to an alias:
   ```bash
   uv run --with openai poc/check_tool_calling.py --base-url "$INFERENCE_API_BASE" --api-key "$INFERENCE_API_KEY" --model <GLM_ID>
   uv run --with openai poc/check_tool_calling.py --base-url "$INFERENCE_API_BASE" --api-key "$INFERENCE_API_KEY" --model <QWEN_ID>
   uv run --with openai poc/check_tool_calling.py --base-url "$INFERENCE_API_BASE" --api-key "$INFERENCE_API_KEY" --model <JUDGE_ID>
   ```
4. Optional: `uv run --with httpx poc/check_inferx_metrics.py --prometheus "$INFERENCE_PROMETHEUS_URL"` to see which inference metrics exist, then adjust `config/z.yaml` → `inference.prometheus.queries`.
5. Service accounts with **separate read and write tokens** for GitLab and Jira (and Logos if used). Write tokens are for the executor only.

## 1. Master prompt
Open Claude Code at the repo root and paste:

> You are the tech lead of Z. Read `CLAUDE.md`, then every file in `docs/` in numbered order, then `docs/BUILD.md`, before doing anything.
>
> Execute BUILD.md. Do phase A yourself, sequentially. Then run the eight phase-B workstreams **in parallel**, each delegated to a sub-agent working in its own git worktree, with these instructions: only edit your own directory, respect the frozen contracts, finish all P0 items with green tests before any P1 item, and report your done-criteria with evidence. Then do phase C yourself: merge, integrate, end-to-end tests, acceptance.
>
> Before writing code that depends on OpenHands SDK, LiteLLM, MCP, Textual, Playwright, the Kubernetes client or exporter metric names, check the current documentation. If an assumption in the spec is wrong, stop and tell me with a proposed fix instead of working around it.
>
> Start by showing me your execution plan (split, order, risks), then continue without waiting for approval unless a decision is irreversible.

---

## 2. Phase A — foundations and contracts (sequential)
1. **Monorepo**: `uv` workspace (`server`, `runner`, `cli`, `packages/z-common`), `ui/` with pnpm, `ruff`, `mypy`, `pytest` (no `pre-commit`: it had no hooks configured and only pulled `virtualenv` into the dev tree; CI runs the same checks). Copy `ci/github/ci.yml` to `.github/workflows/` with the jobs that can run now, `ci/renovate.json` to the root, `ci/CODEOWNERS.example` to `.github/CODEOWNERS`, create `security/exceptions.yaml` and `security/blocked-versions.yaml` from the examples.
2. **Contracts** in `contracts/` from `03-domain-and-contracts.md`:
   - `openapi.yaml` (public + internal API, problem details, SSE endpoints documented);
   - `events.schema.json` (envelope + every event payload);
   - `agent.schema.json`, `playbook.schema.json` (incl. trigger with `owner_user` and templated `inputs`), `connector.schema.json` (every key of 03 §4, operation-id rules), `pack.schema.json`, `skill-frontmatter.schema.json`, `objective.schema.json`, `eval-case.schema.json` (check enum of 08 §6), `mission-plan.schema.json`;
   - `action.schema.json` (action envelope, preview kinds, `gitlab.merge_requests.create` payload);
   - a **pack lint** test: every playbook step agent is in `team`, every agent skill exists, every eval file exists, `writes` are full ids, write ids never appear in agent `tools`, fan-out sources have an output schema;
   - `config.schema.json` for `config/z.yaml`;
   - example documents for each schema under `contracts/examples/` and a test that validates them.
3. **`z-common`**: Pydantic models from the contracts, shared by server, runner and cli.
4. **Database**: SQLAlchemy models, Alembic initial migration, pgvector extension.
5. **Fakes**: `tests/fakes/fake_llm` (scriptable OpenAI-compatible server: text, tool calls, malformed calls, slow responses), `tests/fakes/fake_tools` (fake Jira, GitLab, DefectDojo, Logos from `packs/ticketing/connectors/logos.openapi.json`, CMDB, Confluence), `tests/fakes/fake_prometheus`, `tests/fixtures/injection/` (injection corpus), `tests/fakes/fake_gitlab_oidc` (discovery, JWKS, authorize, token, userinfo with `groups`, device flow; users and groups scriptable per test).
6. **Compose skeleton**: postgres (init script for databases `z` and `litellm`), litellm (mounted config), placeholders for server and ui; networks `edge`, `core`, `runners` (`name: z_runners`, internal), `egress`.
7. **Demo pack** (`packs/demo`): one agent, one skill, one playbook (`summarize-file`), used by every workstream's tests.

**Exit**: contract tests green, compose starts postgres and litellm, commit and tag `contracts-v1`. Contracts are frozen; changes go through the main session only.

---

## 3. Phase B — eight workstreams in parallel
Each: one sub-agent, one worktree (`git worktree add ../z-b1 -b build/b1`), only its directory, tests green.

### B1 — `server/` core: api, scheduler, monitor
- **P0**: REST endpoints of `openapi.yaml` whose owner is B1 in 03 §5 (missions, jobs, review, agents, skills, playbooks, tasks, triggers, sessions, usage, audit), SSE with `Last-Event-ID`, OIDC + dev single-user mode, RBAC; pack loader and validation; scheduler (SKIP LOCKED queue, LISTEN/NOTIFY, concurrency caps global (`executor.max_concurrency`), per mission (`executor.default_concurrency`) and per fan-out step (`fanout.concurrency`), with no per-agent cap, retries with backoff for transient errors, cancel, pause/resume, checkpoints and resume after crash, budgets, timeouts); planner hand-off (orchestrator job) and fan-out expansion; triggers (schedule, Jira label, GitLab assignee, webhook with HMAC); `Executor` interface with `DockerExecutor` (socket proxy), `ProcessExecutor` (dev only) and `KubernetesExecutor` (Jobs in `z-runners`, per-job Secret for the job token, deadlines, TTL, optional RuntimeClass); LiteLLM virtual key per mission (create with a `models` allowlist from the data class, metadata, update on class raise, revoke); usage aggregation from LiteLLM spend logs; audit hash chain + export; `/metrics`.
- **P0** (also): agents CRUD with display name, handle, avatar, instructions, persona; duplicate; rename keeps history.
- **P0 identity and access** (`12-identity-access-and-admin.md`): GitLab OIDC sign-in (PKCE, state, nonce, JWKS validation, userinfo groups), CLI device flow + loopback fallback and `auth/cli/exchange`, server-side sessions, CSRF, step-up, JIT provisioning, hourly group re-evaluation and deprovisioning, personal API tokens, local accounts (bootstrap admin, generic test accounts in non-production, break-glass); RBAC (permissions, roles, scoped role bindings, group and team mapping with dry run, deny-by-default route registry, separation-of-duties rules); admin endpoints; audit log (every mutating route in the same transaction, append-only DB grants, hash chain, signed hourly checkpoints, export, syslog/HTTPS forwarding, `verify`); security headers, rate limits, SSRF guard on admin-entered URLs.
- **P0** (also): inference monitor (Prometheus queries from config, `/v1/models` poll, synthetic tool-call probe) for the Inference screen.
- **P1**: analytics endpoints, notifications (Mattermost, Slack, email, webhook), Z MCP server (`/mcp`), session storage for the CLI, objectives and assignments (scheduler checks, metrics, proposed missions, read-only auto-start within budget), agent builder (`POST /agents/draft`).
- **Done when**: scheduler tests (concurrency, budgets, retries, cancel, resume), all tests of `12` §6 (route permissions, role matrix, OIDC negative cases, deprovisioning, separation of duties, audit coverage and chain); a `demo` mission runs end to end with the fake LLM through both Docker and process executors.

### B2 — `runner/`: agent jobs
- **P0**: `z-runner --job`; `AgentBackend` + `OpenHandsBackend`; workspace modes; neutral tools → gateway MCP + local tools; built-ins `propose_action` (only for the step's `writes`), `ask_human`, `spawn_subagent`, `search_memory`; tools `code` (sandboxed Python) and `documents` (md, docx, pptx, xlsx, pdf, csv); staging of workspace inputs through the judge; guardrails (workspace jail, network block feedback, allowed tools, budgets, redaction, harness confirmation → `needs_attention`); output validation and repair; loop detection; escalation; events and checkpoints; `inject_message` for "Guide the agent"; image `z-runner` (non-root, read-only rootfs).
- **P1**: `report_objective`; take-over support (workspace export/import); terminal recording (asciicast) artifacts.
- **Done when**: tests with the fake LLM for success, repair, denied write, denied network, budget exceeded, loop, resume from checkpoint; a `demo` job runs against the real LiteLLM.

### B3 — `server/` tool gateway, connectors and executor
- **P0**: connector endpoints whose owner is B3 in 03 §5 (CRUD, import, test, exposure review); per-job MCP endpoint; operation ids (03 §4) and the classifier floor (05 §3); operation allowlist; credential injection from secrets backends (env, file, Kubernetes, Vault); rate limits; redaction; judge injection scan hook; data-class raise call to z-server; large-response artifacts; connector kinds `mcp`, `openapi`, `rest`, `builtin` (`web` via the egress proxy); seed connectors GitLab, Jira, DefectDojo, Logos (stub spec), CMDB (rest), Confluence (read); health probes; action executor (idempotent, preview builders for GitLab MR/issue/note and Jira issue/comment, git helper for MRs, two-approver rule for destructive).
- **P1**: `graphql`, `cli`, `database`, `browser` (recorded flows run by the `z-browser` worker), `webhook` kinds; connector builder (spec, docs, browser recording, MCP discovery); token expiry tracking and rotation flow; **Microsoft 365** via Graph (Outlook mail and calendar, Teams, SharePoint, OneDrive) with two app registrations (read app for the gateway, write app for the executor), delegated user connections (OAuth + PKCE) and application permissions; Mattermost and Slack connectors; **channels** (Teams bot with Adaptive Card approvals, Slack, Mattermost, inbound email); remaining catalog templates of `05-connectors.md` §7.
- **Done when**: policy tests (exposure, classes, write blocked from agents), redaction tests, idempotence tests against fake GitLab/Jira, OpenAPI import test on the fake Logos spec.

### B4 — `cli/`: `z`
- **P0**: Typer subcommands of `10-cli.md` with `--json`; `z login` with GitLab (device flow, loopback PKCE fallback) and keyring, `z whoami`, `z admin` commands of `12` §3; `z logs -f`; `z review/approve/reject/answer`; headless `-p` with `--output-format` and exit codes; interactive session (Textual inline): welcome box, live agent trees, inline approval prompts, input with `@`/`#`/`/` completion, status line, interrupt, autonomy cycle, plan mode.
- **P1**: `z top`; sessions `-c`/`--resume`; `/export`; `z mcp serve`; themes; completions; `zctl` alias.
- **Done when**: contract tests against a mock server; Textual snapshot tests of the session and `z top`; headless e2e on the demo pack.

### B5 — `ui/`: web app
- **P0**: shell (sidebar + icon rail), tokens and the four themes and typefaces from `design/tokens.css`; screens 1–4 and 9–11 and 13 of `09-web-ui.md` (Mission control, Agent at work, New mission, Review, Connectors, Inference and usage, History, Settings); SSE live updates; ⌘K palette; generated client; MSW mocks.
- **P0** (also): New agent / Rename on the Agents screen, avatars and handles everywhere an agent appears.
- **P0** (also): Agents and skills screen (roster, rename, duplicate, skills tab); **Sign in** and **Admin** screens (all tabs of `12` §3, diff before save, step-up prompt); permission-aware UI (hide or disable what the user cannot do, never rely on it for security).
- **P1**: Tasks, Playbooks, Learning, Mobile layout, plus the unmocked screens (Connector builder, Trace replay, Evals, Analytics, Agent builder, Objectives, Settings → Connections, Channels).
- **Done when**: every P0 screen works on mocks and matches its mockup; `tsc`, eslint clean; component tests for `EventTimeline`, `ActionReview`, `SchemaForm`, `StackedBars`; axe accessibility check on each screen.

### B6 — `server/` learning, judge and evals
- **P0**: judge service (injection scan of tool results and staged inputs, pre-action duplicate and policy checks, data classification); routing policy (key allowlists per class, raise on C2+ results, external fallback off) and the optional LiteLLM callback; eval runner (`POST /evals`, deterministic checks + rubric grading); memory store (pgvector) with approval.
- **P1**: signals capture from every source in `08-learning-and-evals.md`; learner job (clustering, proposal drafting with `z-heavy`); shadow replay; gates; apply/rollback with versioning; agent builder (`POST /agents/draft`).
- **Done when**: routing tests (C2 never external, fallback off), judge tests on an injection corpus in `tests/fixtures/injection/`, eval runner tests, learning gate test (regression → discarded).

### B7 — `packs/`: agents, skills, playbooks, evals
- **P0**: complete the seed packs `core`, `vuln`, `ticketing`, `dev`, `archi-isp` from the seeds in this repo: agent YAMLs, skills, playbooks, output schemas, connector templates, eval suites (≥ 10 cases per agent, ≥ 5 per skill), fixtures (incl. `dev/evals/fixtures/maven-app*`).
- **P1**: `workplace` pack (needs M365); Browser Agent flows if Logos has no API; more playbooks from `01-product.md` §5.
- Skills are written for open-weight models: short numbered steps, explicit output format with an example, no implied knowledge.
- **Done when**: everything validates against the schemas; evals run on the fake LLM; a real eval run on `z-fast` and `z-heavy` is recorded.

### B8 — `deploy/` and CI
- **P0**: CI from `ci/` and `13-ci-cd.md`: GitHub workflows (`ci`, `images`, `chart`, `e2e`) with every action pinned by SHA, `ci/scripts/install-scanners.sh` with verified checksums, `verify-signatures.sh`, `check_exceptions.py`, blocked-versions check; `.gitlab-ci.yml` for the internal mirror with staging and protected production deploy to k3s. Dockerfiles (multi-stage, non-root, minimal, multi-arch) for z-server, z-runner, z-browser, z-ui; egress proxy config generated from `egress.mirrors`; `deploy/compose/docker-compose.yml` full stack with socket proxy, egress proxy and networks; LiteLLM callback mount; Helm chart `deploy/helm/z` with `values.yaml`, `values-k3s.yaml`, `values-k8s.yaml`, NetworkPolicies, PSA, RBAC, ResourceQuota, migrations Job; GitHub Actions `images.yml`, `chart.yml`; `deploy/README.md`.
- **P1**: `e2e.yml` with kind and k3d smoke installs; ServiceMonitor and a Grafana dashboard JSON for Z metrics; ExternalSecrets example; gVisor how-to for k3s.
- **Done when**: `helm lint`, `helm template … | kubeconform -strict` clean for both values files, `kube-score` without critical findings, compose stack healthy, images build for amd64 and arm64.

---

## 4. Phase C — integration (main session)
1. Merge in order B8, B1, B3, B2, B6, B4, B7, B5. Resolve conflicts without touching contracts.
2. Wire the UI and CLI to the real API (mocks only in dev).
3. E2E suite `tests/e2e/` on compose with the fake LLM and fake tools:
   - triage playbook on 20 fake findings, concurrency 5 respected, tickets proposed, one approval → one Jira create (idempotent on retry);
   - bulk upgrade on 3 fake repos → 3 MRs after approval;
   - injection string in a fake ticket → judge flag, plan unchanged, event visible;
   - `curl` in the sandbox → `denied`; direct write call → `denied`;
   - budget exceeded → `budget_exceeded`; runner killed mid-job → resumed from checkpoint;
   - destructive action with one approval → not applied;
   - C2 mission with an external provider enabled → the key allows internal models only; a C1 mission becomes C2 after a Jira result and loses external models;
   - pack connector rule marking `accounts.disable` as `write` → still `destructive`, two approvers required;
   - injection string inside a cloned repo README → flagged at staging;
   - fresh install: `z-bootstrap` signs in, configures OIDC and the admin group mapping, then is disabled when the first GitLab admin signs in;
   - user in GitLab group `cyber/z/z-operators` signs in through fake GitLab OIDC, can start a mission but gets 403 on approve and on admin routes; added to `z-approvers` → can approve after re-evaluation; removed from `cyber` → sessions revoked;
   - every mutating call in the e2e run has an audit event; `z admin audit verify` passes; tampering one row makes it fail;
   - ZAP baseline on the UI and API has no high alerts;
   - agent renamed "Atlas" is reachable as `@atlas` from web and CLI;
   - (P1) objective off track → missions proposed, read-only one auto-started within budget, write proposed only;
   - (P1) fake Teams message `@atlas …` → mission, approval card, post after approval.
4. Real smoke test on inferX: `z run weekly-vuln-triage` on a test product, `z eval vuln-analyst --models z-fast,z-heavy`, Inference screen shows live metrics.
5. Install on k3s with `values-k3s.yaml` and on kind with `values-k8s.yaml`.
6. Write `ACCEPTANCE.md`: each criterion of `01-product.md` §6, evidence, status.

## 5. Tips
- Spend time reviewing the phase A contracts before launching phase B; that is where speed and consistency are decided.
- Without built-in sub-agent worktrees, open eight Claude Code sessions, one per worktree, each with: "Execute workstream B<n> of docs/BUILD.md."
- If a model handles tools poorly, fix it in B2 (repair, smaller toolsets, constrained decoding) and B7 (simpler skills), or move that agent to `z-heavy`; do not loosen guardrails.
