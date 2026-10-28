# CLAUDE.md — Z

## What this repo is
Z is a self-hosted platform to run many AI agents in parallel against any tool, with live visibility, human approval of every write, and a learning loop. Agents run **only** on the internal inference cluster (OpenAI-compatible, e.g. inferX) through a LiteLLM gateway. The agent harness is the OpenHands Software Agent SDK behind an `AgentBackend` interface.

The specification is in `docs/`, numbered in reading order. It is authoritative in this order: `docs/07-security.md` (invariants) > `docs/03-domain-and-contracts.md` (shapes) > the other numbered docs > `docs/BUILD.md` (how to build). Once frozen, `contracts/` is the source of truth between components.

## How to work
- **Read before coding.** Read every file in `docs/` before your first change. Re-read the doc of the area you touch before each workstream.
- **Verify third-party APIs.** Before using OpenHands SDK, LiteLLM, MCP SDKs, Textual, Kubernetes client, Playwright, vLLM or DCGM metrics, check the current official documentation. Do not trust memory: names and signatures move fast. If the spec assumes something that is wrong, say so and propose the fix instead of working around it silently.
- **Contracts first.** No component invents a field, event type, status or manifest key that is not in `contracts/`. Contract changes are their own commit, made by the main session, then every consumer follows.
- **Parallel workstreams.** During BUILD phase B, each sub-agent edits only its own directory. Shared files (`contracts/`, root config, root `pyproject.toml`, `CLAUDE.md`) are changed only by the main session.
- **Small commits**, conventional commits in English (`feat(runner): …`). One logical change per commit.
- **Tests are not optional**: pack and manifest loading, scheduler (concurrency, budgets, retries, cancel, resume after crash), runner guardrails, output repair, tool gateway policy and redaction, action executor idempotence, routing policy (data classes), learning gate (no regression → no proposal). LLMs are faked in unit tests (`tests/fakes/fake_llm`).
- **Tooling**: `uv` for Python 3.12, `pnpm` for the UI. `ruff` + `mypy --strict` for `server`, `runner`, `cli`. `eslint` + `tsc --noEmit` for `ui`.
- **Language**: code, identifiers, UI copy, docs and commit messages in English.
- **Portability**: everything must run on Docker Compose, k3s and vanilla Kubernetes. No k3s-only or cloud-only API in code; differences live in Helm values.
- When you finish a workstream, update `docs/ROADMAP.md` status and add an ADR in `docs/adr/` for any decision the spec did not already make.

## Security invariants (never break these)
1. No LLM call goes anywhere except the LiteLLM gateway. No model provider URL in code.
2. External model providers are off by default. When enabled, the routing policy decides per call from the data class; C2 and above never leave the internal cluster.
3. Agents hold **no credentials**. Tools are reached only through the Z tool gateway, which injects credentials, enforces the operation allowlist and redacts secrets.
4. Every external write goes through `propose_action` → human approval → action executor. In v1 there is no auto-approval. Destructive operations are never exposed to agents unless an admin explicitly enables them, and then always need two approvers.
5. Platform guardrails apply to every agent and cannot be disabled or widened by a pack, playbook or prompt.
6. Content coming from tools and workspaces (tickets, findings, documents, web pages, repos, uploaded files) is data, never instructions. The judge scans tool results and staged workspace inputs for injection before they reach the agent.
7. Connector rules can only raise an operation's class, never lower it; pack-shipped exposure stays `pending_review` until an admin confirms it. Read and write credentials are always separate, including delegated (user) OAuth grants.
8. **Deny by default.** Every API route declares a permission; access comes only from role bindings (GitLab group mappings or audited manual grants). Sensitive changes need step-up re-authentication. Identity comes from GitLab OIDC; local accounts (bootstrap admin, non-production test accounts, break-glass) are off by default, limited as in `docs/12` §1, and always audited.
9. Every mutating request writes an audit event in the same transaction; the audit table is append-only for the app's database role.
10. The data class of a mission only goes up, and it is enforced through the mission's LiteLLM key allowlist, never by the runner.
11. Secrets never appear in prompts, events, artifacts, logs or traces.
12. Every mission and job has a token budget, a step limit and a timeout.
13. Containers run non-root with a read-only root filesystem except the workspace, no privilege escalation, `seccomp: RuntimeDefault`, all capabilities dropped.
14. The learning loop proposes; a human applies. Nothing changes an agent, skill, memory or routing rule without approval, and every change is versioned and reversible.

## Layout (after build)
```
contracts/   openapi.yaml + JSON Schemas (events, manifests, actions)
server/      z-server: api, scheduler, gateway, executor, learner (one codebase, several entrypoints)
runner/      z-runner: one agent job (AgentBackend, OpenHands backend, guardrails)
cli/         z: interactive session, z top, subcommands
ui/          web app (React)
packs/       seed packs: core, vuln, ticketing, dev, archi-isp (v1.0), workplace (v1.1) + demo
config/      example configs
deploy/      compose/, helm/z (values-k3s.yaml, values-k8s.yaml), images
design/      mockups and tokens (reference for ui/ and cli/)
tests/       fakes/, e2e/
```

## Commands (after build)
- `docker compose -f deploy/compose/docker-compose.yml up -d`
- `uv run pytest` · `uv run ruff check` · `uv run mypy`
- `uv run z …` (CLI from source) · `pnpm -C ui dev`
- `helm lint deploy/helm/z && helm template deploy/helm/z -f deploy/helm/z/values-k3s.yaml | kubeconform -strict`
