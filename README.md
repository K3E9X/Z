# Z

**Z is a self-hosted cockpit for AI agents, for a whole team.** People sign in with their GitLab account and get permissions from their GitLab groups. You launch many agents in parallel, connect them to any tool (Jira, GitLab, internal tools through their API, MCP or a browser), watch what each one does step by step, and approve every write before it happens. Agents get better over time from your decisions, through a learning loop that never applies a change without your approval.

Z runs entirely on your own inference cluster (OpenAI-compatible API, for example **inferX** serving GLM and Qwen). External model APIs are optional and fenced by a data-classification policy.

It works three ways:
- a **web app** (mission control, review, agents and skills, playbooks, learning, connectors, inference and usage);
- a **CLI** that feels like Claude Code (`z`), with a live dashboard (`z top`) and headless mode for scripts and CI;
- an **API** (REST + SSE), plus an MCP server so other agents can start Z missions.

It deploys the same way on **Docker Compose**, **k3s** and **Kubernetes**.

> Status: built. The server, runner, CLI, web app, packs, Compose stack, Helm chart and CI exist and are tested;
> [`docs/ROADMAP.md`](docs/ROADMAP.md) tracks what is done and what is left (acceptance against the real inferX,
> GitLab and Jira). The code was written with Claude Code following [`docs/BUILD.md`](docs/BUILD.md).

---

## Work on it (with Claude Code)

1. Clone this repo and open Claude Code at its root.
2. Do the manual prerequisites in [`docs/BUILD.md` §0](docs/BUILD.md#0-prerequisites-manual): `.env`, model IDs, tool-calling check.
3. Paste the master prompt from [`docs/BUILD.md` §1](docs/BUILD.md#1-master-prompt).

Claude Code reads [`CLAUDE.md`](CLAUDE.md) automatically. Other coding agents read [`AGENTS.md`](AGENTS.md).

## Run it

```bash
# Local, Docker Compose (details, options and the classic Docker image store: deploy/README.md)
cp .env.example .env && cp config/litellm.example.yaml config/litellm.yaml && cp config/z.example.yaml config/z.yaml
for k in POSTGRES_PASSWORD Z_APP_DB_PASSWORD LITELLM_DB_PASSWORD Z_SOCKET_PROXY_TOKEN; do
  sed -i "s|^$k=.*|$k=$(openssl rand -hex 32)|" .env; done
for k in LITELLM_MASTER_KEY LITELLM_SALT_KEY; do sed -i "s|^$k=.*|$k=sk-$(openssl rand -hex 32)|" .env; done
sed -i "s|^Z_SECRET_KEY=.*|Z_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n')|" .env
echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock)" >> .env
docker build -f deploy/images/z-server/Dockerfile -t z-server:local .
docker build -f deploy/images/z-runner/Dockerfile -t z-runner:local .
echo "Z_RUNNER_IMAGE=$(docker image inspect --format '{{index .RepoDigests 0}}' z-runner:local)" >> .env   # pinned by digest
docker compose --env-file .env -f deploy/compose/docker-compose.yml up -d --wait
docker compose --env-file .env -f deploy/compose/docker-compose.yml logs z-server | grep bootstrap   # first sign-in
open http://localhost:8080

# k3s and Kubernetes: create the release namespace and make it enforce Pod Security `restricted` first
# (not --create-namespace: Helm would create it unlabelled; deploy/README.md, docs/11 §2)
kubectl create namespace z
kubectl label namespace z pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/enforce-version=latest
helm upgrade --install z deploy/helm/z -n z -f deploy/helm/z/values-k3s.yaml -f my-values.yaml   # k3s
helm upgrade --install z deploy/helm/z -n z -f deploy/helm/z/values-k8s.yaml -f my-values.yaml   # Kubernetes

# CLI
uv tool install ./cli --with ./packages/z-common && z login && z   # z-common is not on PyPI (cli/README.md)
```

## Repository map

| Path | What it holds |
|---|---|
| `CLAUDE.md`, `AGENTS.md` | Rules for coding agents working on this repo |
| `docs/` | The full specification, numbered in reading order, plus `BUILD.md` and `ROADMAP.md` |
| `docs/adr/` | Architecture decision records |
| `design/` | Web and CLI mockups (standalone HTML), screenshots, design tokens |
| `config/` | Example configuration: model gateway, platform policy |
| `packs/` | Seed packs: agents, skills, playbooks, connectors, objectives, evals for `core`, `vuln`, `ticketing`, `dev`, `archi-isp` (v1.0) and `workplace` (v1.1) |
| `poc/` | Scripts to validate the inference cluster before building |
| `contracts/` | OpenAPI and JSON Schemas: the source of truth between components |
| `server/`, `runner/`, `cli/`, `browser/`, `packages/z-common/` | z-server, z-runner, the `z` CLI, the z-browser worker, shared models |
| `ui/` | The web app (React) |
| `deploy/` | Compose stack, Helm chart, images, per-environment values |
| `tests/`, `ci/`, `security/`, `.github/`, `.gitlab-ci.yml` | Fakes and e2e tests, CI scripts, scanner policies and exceptions, pipelines |

## Reading order

1. [`docs/01-product.md`](docs/01-product.md): what Z does and does not do
2. [`docs/02-architecture.md`](docs/02-architecture.md): components, flows, stack
3. [`docs/03-domain-and-contracts.md`](docs/03-domain-and-contracts.md): entities, statuses, manifests, API
4. [`docs/04-runtime.md`](docs/04-runtime.md): runner, orchestrator, guardrails, judge
5. [`docs/05-connectors.md`](docs/05-connectors.md): connecting any tool
6. [`docs/06-inference-and-usage.md`](docs/06-inference-and-usage.md): inferX, model routing, tokens, budgets
7. [`docs/07-security.md`](docs/07-security.md): threat model and invariants
8. [`docs/08-learning-and-evals.md`](docs/08-learning-and-evals.md): how agents improve
9. [`docs/09-web-ui.md`](docs/09-web-ui.md): design system and screens
10. [`docs/10-cli.md`](docs/10-cli.md): the `z` command line
11. [`docs/11-deploy.md`](docs/11-deploy.md): Docker, k3s, Kubernetes
12. [`docs/12-identity-access-and-admin.md`](docs/12-identity-access-and-admin.md): GitLab sign-in, RBAC, admin area, audit log
13. [`docs/13-ci-cd.md`](docs/13-ci-cd.md): CI pipelines (GitHub Actions and GitLab CI), security scans, deploy
14. [`docs/BUILD.md`](docs/BUILD.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md)

## Before you push

Keep this repository **private** if it carries anything about your company. The kit contains no hostnames, credentials or internal URLs: everything environment-specific lives in `.env`, `config/*.yaml` and Helm values, which are git-ignored except for the `*.example*` files.
