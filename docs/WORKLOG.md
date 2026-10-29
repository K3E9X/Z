# Build worklog

Working journal of the build (newest first). It exists so the work can be resumed at any point without
re-deriving context: what was done, how it was verified, what is next, and the open questions.
Status by workstream lives in `docs/ROADMAP.md`; decisions in `docs/adr/`.

## How the work is organised
- One verified vertical slice at a time; every slice ships with tests against real dependencies
  (Postgres container, Docker daemon, LiteLLM). Test doubles live only under `tests/`.
- Parallel workstreams run as sub-agents in git worktrees, each editing only its own directory
  (`ui/`, `cli/`, `runner/`); the main session owns `contracts/`, root config and `server/`, reviews and merges.
- Gates before every push: `uv run ruff check . && uv run ruff format --check . && uv run mypy server/src
  runner/src cli/src packages/z-common/src deploy/images/z-socket-proxy/socket_proxy.py && uv run pytest`.
- Pull request: https://github.com/K3E9X/Z/pull/1 (branch `claude/inspiring-planck-per8ii`).

## Environment facts (target)
- Ticketing: **Jira Cloud** (REST API v3, Atlassian Document Format for rich text; auth: API token with
  basic auth `email:token` for service accounts, OAuth 2.0 3LO for delegated user grants).
- Still to receive: inferX URL + model ids (GLM, Qwen, judge, embedding + dim), Prometheus URL, GitLab URL/version
  and groups, deployment target. Pending decision: GitLab group inheritance for role mapping.

## Journal

### Session 3 (autonomous, continued)
- Query connectors (ADR 0020): `graphql` (declared documents; mutations refused in read operations at
  validation, lint and call time) and `database` (PostgreSQL, declared SELECT only, READ ONLY transaction,
  10 s statement timeout, 1000-row cap, rollback). A changed document unexposes its operation.
- Audit worker (ADR 0021): scheduled signed checkpoints (own short transaction, never network I/O under the chain
  lock), at-least-once forwarding with a cursor per target: syslog RFC 5424 over TLS with octet counting (tested
  against a real TLS listener) and JSON lines over HTTPS with a bearer `token_ref`. Delivery state shown in
  Admin → Audit. `verify` now anchors a retention-purged chain on a signed checkpoint; purge SQL in
  deploy/README.md.
- Z as an MCP server (ADR 0022): `/mcp` (bearer tokens only, cookies ignored) and `z mcp serve` (stdio) share
  `z_common.mcp_tools`; each tool runs the REST operation in-process as the caller, so permissions, scopes,
  audit and rate limits are unchanged. No approval tool by design.
- Notifications (ADR 0023): `notification_outbox` (migration 0004) written by `events.append`, break-glass sign-in
  and the health monitor in the same transaction; worker delivers to Slack/Mattermost/Teams webhooks, signed
  webhooks and SMTP, with retries/backoff, per-target delivery state and budget alerts once per period.
  Admin → Notifications tab.
- inferX (ADR 0024, from the user's inferX docs): pinned catalogue models, private gateway, Retry-After-aware
  router retries, then Z requeues jobs that report `llm_unavailable` from their checkpoint; `ixk_` keys redacted and
  in gitleaks. Open requirement: no external fallback (genapi) on any model Z's key can call; no shared embeddings
  model yet (memory search stays off).
- CI: license gate on installed packages (found and excluded a closed `lmnr-claude-code-proxy` binary pulled in by
  openhands-sdk), kind smoke install under PSS restricted, release notes from conventional commits, ZAP baseline.
- Verified LiteLLM 1.103.4 behaviour in its installed source (docs.litellm.ai is not reachable from this sandbox):
  provider headers come back as `llm_provider-*`, the router honours `Retry-After` for a single deployment.
- First fully green CI on PR #1 (ef60c4fe): python, ui, lint, security, images, chart, policy, compose e2e with a
  real mission + ZAP baseline, kind smoke install under PSS restricted.
- Found by checking the code against the spec: the workplace pack's m365 connector pointed at a `builtin:` spec
  that nothing served (now a shipped Graph v1.0 subset, and the pack lint refuses such specs); docs/06 §5 metrics
  were missing from `/metrics` (now served, plus a Grafana dashboard in the chart); LiteLLM fetched its price map
  from GitHub at start (now bundled).
- inference guard (ADR 0024): LiteLLM callback refusing answers served through an inferX external fallback for
  C2/C3 keys; verified against the real LiteLLM 1.105.0 image with a fake upstream (200 without the header, 403
  with it).
- Dropped pre-commit (no hooks configured; its virtualenv dependency got GHSA-cx6f-vjhg-g3cc).
- Raw request bodies for operations whose spec declares text/* or octet-stream (max 4 MB): m365 `files.upload`
  for small text files in OneDrive/SharePoint.
- Personal notifications: Settings → Notifications, email about one's own missions (`notify_prefs:<user>`).
- cli connectors (ADR 0025): argv template with no shell, agent arguments can never start with `-`, credentials
  only in the tool's environment, image pinned by digest and allowlisted in `executor.tool_images`; Docker runs
  through the socket proxy's tool profile (uid 65534, verified on real Docker), Kubernetes as a Job with a per-run
  Secret. The connector builder drafts them (`POST /connectors/import` source `cli`).
- GraphQL import by introspection (ADR 0020 §5): one document per query field (read) and per mutation (at least a
  write: read verbs are dropped from mutation ids), scalar leaves two levels deep, typed variables.
- Mattermost tool connector (workplace pack): API v4 subset checked against mattermost/mattermost api/v4/source;
  posts carry `props.z_action` set by the executor (the body schema gives agents no `props`), so recovery after a
  crash searches the channel's posts since the proposal instead of posting twice. Rule ids need a read verb for
  the pack linter (`threads.get`, not `posts.thread`).
- Trap: FastAPI fakes defined inside a function must not use `from __future__ import annotations` with
  `Annotated[..., Depends(local)]` (the string annotation cannot be resolved and becomes a query parameter).
- Found by reading the grader: eval checks only saw `output`, so every check on `actions[...]`/`report` and the
  bare paths in three packs (`priority`, `verdict`, `playbook`) could never pass. The grader now builds the whole
  job result of the contract; the linter refuses unrooted paths and warns below docs/08 minimums (10 per agent,
  5 per skill). Eval suites written by five sub-agents (one per pack group), reviewed and committed per pack.
- Connector builder "from docs": planner-model draft of `rest` operations, sanitised (relative paths, no header
  arguments, classifier classes, reads only proposed for exposure); database and webhook kinds stay YAML.
- Eval runner made to behave like real missions (found by the five eval sub-agents, checked in the code): output
  schema from the case's json_schema check (or a generic object), literal-text fixtures, the agent's connectors
  attached, skill cases filtered by their mark, contract schemas with their $refs, changes.patch in `files`
  workspaces, and case missions stuck on needs_attention end as failed instead of holding the run.
- Pack design gaps reported (decided later in ADR 0029): no output
  schemas for the archi-isp and workplace agents (checks use report.md), no `read` tool for assistant, researcher
  and the core agents (cases pass data inline), no edit/documents tool for ticket-manager and vuln-analyst (no
  report.md), the browser-flows "CSV artifact" step has no tool, meeting-prep "tasks" has no m365 operation.
- Slack tool connector (ADR 0026), verified against slackapi/python-slack-sdk (docs.slack.dev is not reachable
  here): `error_body` contract field for 2xx error answers; found on the way that `health.operation` was never
  run by the connection test or the health monitor (DefectDojo's probe included).
- Independent security review of the day's code (fresh sub-agent, code only), each finding verified then fixed:
  shadow replay / try copied other teams' missions at the default data class (now: only missions the starter may
  read, at their class); cli `-1` and swallowed optional flags; Docker tool logs buffered whole; `..` path
  parameters reaching parent endpoints with the read credential; tool Secrets not owned by their Job; drafted
  GETs without a read verb classed read; tool error text not redacted; spec and introspection fetches buffered
  whole; planted chat markers skipping a post; streamed fallback answers passing the inference guard (fixed and
  verified in the LiteLLM image); RestartPolicy key case; credential literals in cli.env. Left: Docker tool
  containers have no startup sweep (their env holds the credential until z-server's cleanup runs).
- inferX key from the web app (ADR 0027, asked by the owner): LiteLLM credentials API checked in the installed source
  and proven on the litellm 1.105.0 image with Postgres (env default; API key from the next call and after a
  restart; wrong key reported without echo; value encrypted in LiteLLM's DB). Needs `STORE_MODEL_IN_DB=True` and a
  kept `LITELLM_SALT_KEY`. Z keeps the fingerprint, who and when, never the key.
- Google Workspace (ADR 0028): `google` (Gmail, Calendar, Drive) and `gchat` connectors, delegated only, with
  `oauth.provider: google`. developers.google.com is not reachable from this sandbox: paths, parameters and scopes
  were checked against the discovery documents (`www.googleapis.com/discovery/v1/apis/<api>/<v>/rest`, reachable)
  and Google's OpenID configuration. Unauthenticated probes showed gmail/calendar/drive on www.googleapis.com and
  Chat only on chat.googleapis.com, hence two connectors. The fake enforces Google's rules (no offline_access,
  refresh token only with access_type=offline, gmail.metadata cannot use q, requestId idempotence).
- Microsoft 365: Graph write bodies closed (an agent could add bccRecipients or replyTo, visible only in a JSON
  dump), readable previews, events idempotent by `transactionId`. learn.microsoft.com and graph.microsoft.com are
  blocked here; the official description `microsoftgraph/msgraph-metadata` (openapi/v1.0) on GitHub is reachable
  and was the reference. The `m365` pack connector is now tested end to end against the fake.
- Independent review of the Google/M365 work (ADR 0028 consequences): fixed top-level keys merged into write
  bodies past the preview (all OpenAPI writes), RFC 2047 encoded words decoded into hidden recipients, calendar
  ownership by `organizer.self`, `..` calendar ids, Chat reply previews; M365 events no longer count as
  idempotent. Lesson: a preview is only as good as the closed schema of everything the writer sends.
- Pack design gaps closed (ADR 0029, the owner asked for autonomous decisions): only dev-engineer could write
  report.md and the runner never asked for it, so 80 report-based eval cases could not pass. Now every job is
  asked for its report and may write it; `read` for agents that receive files; browser-agent `documents`;
  Microsoft To Do in the m365 subset for meeting-prep.
- Personal connections now record the account of each grant (`openid email`, ID token claims) and refuse read
  and write grants of different accounts; the identity field had never been filled.
- Trap: connector slugs allow `-` but operation ids do not: a hyphenated pack connector seeded fine and then no
  operation could be approved (422). The pack linter now refuses it (the connector API already did).
- Trap: `pkill -f <pattern>` with the pattern in the same command line kills the shell (exit 144): kill by PID.
- Trap: `config/litellm.yaml` is git-ignored (local copy): edit `config/litellm.example.yaml` for the repository.
- Trap: a change to `contracts/*.schema.json` also changes the UI types (openapi-typescript inlines external refs):
  run `pnpm -C ui generate:api` after every contract change, and never push before the local UI checks finish
  (CI went red on b8dd30f8 for exactly this).
- Traps: the dependency test caught an undeclared `anyio` import in the CLI (use asyncio.run); `MissionStatus`
  ends in `succeeded`, not `completed`; ZModel drops None fields, so read optional status keys with `.get`.

### Session 2 (autonomous night build)
- Runner (B2) merged (113 tests). Aligned to the contract (`ControlMessage`).
- **First real mission on compose** (`tests/e2e/test_compose_mission.py`, marker e2e): z-server all ->
  z-socket-proxy -> hardened runner container -> OpenHands -> LiteLLM virtual key -> fake LLM. It found three
  real bugs: python-multipart missing from the server image, runner URL missing `/api/v1`, and no way to see why
  a runner died (fixed: watchdog keeps the redacted log tail + exit code in the job error).
- `tests/unit/test_dependencies.py`: every direct import must be declared by its package.
- Jira Cloud REST v3 connector (`packs/ticketing/connectors/jira.yaml`) + fake Jira v3.
- Container restart mid-session stopped the sub-agents; briefs now live in `.claude/briefs/*.md` so they can be
  relaunched in their existing worktrees.

- Gateway (B3): per-job MCP endpoint, executor (Jira Cloud, GitLab with git helper), connector admin
  (create/patch/test/import), health monitor, git_clone workspaces. Jira and GitLab pack connectors are now REST
  (Jira Cloud v3, GitLab v4): their MCP versions needed external servers with placeholder commands.
- B1: usage/budgets/providers/inference/analytics/global stream; triggers, signed webhooks, cron schedules, Jira
  polling, tasks board.
- CLI (B4) merged (41 tests). Server fixes from its report: CLI sign-in audited as the user, agent handles
  resolved, LiteLLM key errors are 503 problems.
- Open requests from the CLI report (need contract changes): a CLI step-up flow (POST /auth/step-up refuses CLI
  sessions, so admin writes from the CLI fail with exit 4 in OIDC mode) and GET /questions/{id}.

- B1 (night run): draftPlan (planner model via the platform key, validated, repaired once, ordered), sessions
  (server-side conversations; the CLI uses them for every prompt, `z -c`, `z --resume`, `z --session`,
  `/resume`), saveMissionAsPlaybook, honest take-over (503), objectives and assignments with suggestions in
  Review, read-only auto-start within budget and the `report_objective` endpoint; fake Jira JQL understands
  date comparisons. Fixed: plans with forward or circular dependencies hung forever; Review showed every
  suggestion to everyone; test Postgres containers leaked their volumes (7 GB, disk full).

- B6 (night run, cont.): judge model stage (raise-only, fenced input, guardrail controls it; ADR 0013),
  pre-action checks in Review (injection, data class, fields, duplicates; a blocked payload cannot be
  approved), routing policy API with versioned rules used by intake, learning signals derived from events,
  eval runs as real missions (writes captured, questions auto-answered, checks + rubric grading), learner
  proposals with before/after eval gates, apply/reject/try, learning worker (ADR 0014). Contract: config
  `judge`, `inference.planner_model`, `learning.run_as|min_signals`. Migration 0002 (eval missions,
  candidates). Lesson: editing config/z.example.yaml during a test run broke 77 tests (the schema had no
  `judge` key) - change the contract first, never edit config while the suite runs.

- B3 (cont.): personal OAuth connections (PKCE, sealed tokens, refresh, revoke) used by user-scope steps and
  actions, app-only OAuth tokens, channels (Slack, Mattermost, Teams with Bot Framework JWT, email relay) with
  identity mapping, judge scan, replies (ADR 0015). Merged the Helm chart (B8) and the web UI (B5). Fixed: OAuth
  app secrets were not checked as references; connectors listed everyone's personal credentials; /metrics was
  open to runners (now bearer token); the learner could run twice with two replicas (advisory lock); hmac
  routes tried to read a Bot Connector JWT as a Z token. Lesson: after a worker restart dockerd can fail on a
  stale containerd: `pkill -9 containerd; rm -f /var/run/docker.pid; nohup dockerd &`.

- B3 (cont.): browser connectors (ADR 0016): `flows` in the connector contract, shared validation
  (`z_common.connectors.browser`), the `z-browser` worker package (Playwright 1.63, fresh context per run, route
  guard), gateway `browser` kind with screenshots as artifacts, executor writes with the write account, builder
  import `source: browser` + UI flow editor, example connector `packs/archi-isp/connectors/fwconsole.yaml`, fake
  FW Console web UI. Image on Microsoft's Playwright base (pinned by digest), compose profile `browser`, Helm
  `browser.enabled`. Found by testing: Playwright's route handler is not called for redirect hops, so an
  allowlisted page could 302 the browser anywhere; the guard now fetches without following redirects and
  checks each Location. Also fixed: the builder dropped the draft's auth when no secret was typed.

- Chat follow-ups (ADR 0017): `channel_threads` (migration 0003, grant to the app role) with a sealed reply
  target and an event cursor; the channels worker posts approval cards (Teams Adaptive Card, Slack thread,
  response URL) that link to Z, questions and outcomes, content capped by `content_up_to`. Fixed: the UI
  add-channel dialog never sent `config` (every add was a 422).
- B8 CI merged (ADR 0018): guard, lint, Python, UI, security, images (build/scan/sign/SBOM), chart policy,
  compose e2e, GitLab mirror with signed k3s deploys. Its first runs found real issues, all fixed: filelock
  CVEs (filelock 4.2.4, litellm library 1.103.4), python base digest younger than 7 days (3.12.15 pinned),
  nginx 1.28 end of life (1.30 + apk upgrade for brotli), pip's vendored certifi in the Playwright base
  (pip removed), kubescape 83.79 % (full CIS 5.7.3 security context; C-0012 excepted for env references),
  compose bake refusing /dev/null as a secret file.
- Take-over (ADR 0019): the workspace is rebuilt from staging + checkpoint delta; read_write edits are folded
  into the checkpoint and the job restarts from it (no attempt used, the agent is told); the scheduler never
  claims a queued job whose old runner is still referenced (reap first) - this also fixes retried
  needs_attention jobs getting a second runner while the first was parked. UI page and z top wording.

- Browser recording by demonstration (ADR 0016 item 9): worker sessions behind the replay guard, unique-selector
  candidates, credentials typed by the worker and recorded as `{{auth.*}}`, arguments as `{{args.x}}`; server
  proxy per admin with audit per input; UI recorder page (live screenshot, click modes, typing, keys, goto,
  extract, undo, save into the connector for review). Proven in the real UI against the fake console. Found by
  the smoke: an empty start URL opened a 404 page and every click recorded `html` - clicks on no element are
  refused now and recordings start at the connector's flow start page.
- CI round 2: images, chart policy and helm green. Fixed shellcheck SC2015 in verify-signatures.sh, a semgrep
  finding on the CLI editor launch (reviewed: local user's EDITOR, argv), and compose never seeding the packs
  (`z-seed` one-shot service; the e2e failed with "no playbook summarize-file").

Lessons / traps:
- Never `pkill -f`/`awk` on a pattern that appears in your own command line (it kills the shell): start
  helpers from a script file and kill by PID.
- `.gitignore` has `*.pem`: placeholder files must not end in .pem (CI could not find `empty-ca.pem`).
- This sandbox's proxy blocks deb.debian.org and dl-cdn.alpinelinux.org: apk/apt steps are only proven in CI.
- Docker Hub rate-limits pulls here (429) for a while; the Hub tag API still gives index digests.
- kubescape C-0211 (CIS 5.7.3) passes only when every field of its checklist is set (seLinuxOptions,
  fsGroupChangePolicy, procMount...).
- Playwright: `page.route`/`context.route` do not see redirect hops; never rely on them alone for egress.
- The z-browser image is ~3.7 GB (base includes all engines): prune docker before building here.
- Never run plain `uv sync` at the root (`package = false` uninstalls the workspace packages): use
  `uv sync --all-packages`.
- This sandbox blocks deb.debian.org: build the runner with
  `--build-arg RUNTIME_IMAGE=python:3.12-bookworm` here (real builds use the pinned slim image + apt mirror).
- After a container restart: `(dockerd > /tmp/dockerd.log 2>&1 &)`.
- "No space left on device": `docker volume prune -f && docker builder prune -f` (anonymous volumes of
  old test containers pile up).
- Compose e2e: `cd deploy/compose && DOCKER_GID=$(stat -c %g /var/run/docker.sock) docker compose --env-file
  ../../.env -f docker-compose.yml -f ../../tests/e2e/compose.fakes.yml -f ../../tests/e2e/compose.e2e.yml up -d
  --wait`, then `uv run pytest -m e2e tests/e2e`.

- Workstreams in flight: runner (B2, sub-agent), web UI (B5, sub-agent), CLI (B4, sub-agent).
- Main session: B3 tool gateway + connectors + action executor (Jira Cloud first), then B1 remaining
  operations, B6, B8.

### Session 1
- Contracts v1, z-common, z-server base (OIDC, RBAC, audit), catalog, missions/scheduler/runner protocol/review,
  deterministic judge, z-socket-proxy. 565 tests + 25 proxy tests. ADR 0011, 0012.
