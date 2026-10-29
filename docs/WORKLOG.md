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

Lessons / traps:
- Never run plain `uv sync` at the root (`package = false` uninstalls the workspace packages): use
  `uv sync --all-packages`.
- This sandbox blocks deb.debian.org: build the runner with
  `--build-arg RUNTIME_IMAGE=python:3.12-bookworm` here (real builds use the pinned slim image + apt mirror).
- After a container restart: `(dockerd > /tmp/dockerd.log 2>&1 &)`.
- Compose e2e: `cd deploy/compose && DOCKER_GID=$(stat -c %g /var/run/docker.sock) docker compose --env-file
  ../../.env -f docker-compose.yml -f ../../tests/e2e/compose.fakes.yml -f ../../tests/e2e/compose.e2e.yml up -d
  --wait`, then `uv run pytest -m e2e tests/e2e`.

- Workstreams in flight: runner (B2, sub-agent), web UI (B5, sub-agent), CLI (B4, sub-agent).
- Main session: B3 tool gateway + connectors + action executor (Jira Cloud first), then B1 remaining
  operations, B6, B8.

### Session 1
- Contracts v1, z-common, z-server base (OIDC, RBAC, audit), catalog, missions/scheduler/runner protocol/review,
  deterministic judge, z-socket-proxy. 565 tests + 25 proxy tests. ADR 0011, 0012.
