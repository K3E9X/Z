# 11 — Deploy: Docker, k3s, Kubernetes

One codebase, three targets. Differences live in configuration, never in code.

| | Docker Compose | k3s | Kubernetes |
|---|---|---|---|
| Use | Local dev, single host, demo | Team server | Shared or larger clusters |
| Executor | `docker` via socket proxy (`process` for dev only) | `kubernetes` | `kubernetes` |
| Ingress | z-ui on `:8080` (Caddy/nginx optional) | Traefik (bundled) | ingress-nginx, Traefik or Gateway API |
| Storage | named volumes | `local-path` (bundled) | any CSI StorageClass |
| NetworkPolicy | Docker networks | enforced (bundled network policy controller) | needs a CNI that enforces it (Cilium, Calico) |
| gVisor | optional runtime `runsc` | optional, containerd config template | optional, per distribution |
| Postgres | container | in-cluster StatefulSet or external | external or CloudNativePG |
| Files | `deploy/compose/` | `deploy/helm/z` + `values-k3s.yaml` | `deploy/helm/z` + `values-k8s.yaml` |

## 1. Docker Compose (`deploy/compose/docker-compose.yml`)
Services: `postgres` (16 + pgvector, init script creating the roles `z`, `z_app`, `litellm` and the databases `z` and `litellm`), `litellm`, `z-migrate` (applies the migrations, then exits), `z-seed` (loads the packs baked into the image into the catalog, then exits), `z-server` (`all` entrypoint, started once both succeeded), `z-ui`, `docker-socket-proxy`, and two optional profiles: `egress` (`egress-proxy`, allows only `Z_EGRESS_MIRRORS`) and `browser` (`z-browser`, the gateway-side browser worker).
Networks: `edge` (z-ui ↔ z-server), `core` (z-server and LiteLLM: the way out to tools and the inference cluster), `db` (internal: Postgres ↔ z-server, LiteLLM, z-migrate, z-seed), `dockerapi` (internal: z-server ↔ socket proxy), `browser` (internal: z-server ↔ z-browser), `runners` (declared with `name: z_runners`, `internal: true`: runners ↔ LiteLLM, z-server internal API + gateway, egress-proxy; no internet), `egress` (egress-proxy and z-browser only, to mirrors and allowlisted apps).
Runners are started by z-server through the socket proxy on the `runners` network with the hardening options of `07-security.md`. Compose builds z-server, z-ui and the socket proxy; the runner image is not a Compose service, so build it yourself. `Z_ENV` defaults to `production`, where z-server runs only a runner image pinned by digest (`Z_RUNNER_IMAGE` must contain `@sha256:`), refuses weak secrets and needs `Z_SOCKET_PROXY_TOKEN`.
```bash
cp .env.example .env && cp config/litellm.example.yaml config/litellm.yaml && cp config/z.example.yaml config/z.yaml
# random secrets (Compose refuses to start without them, z-server refuses weak ones); GNU sed
for k in POSTGRES_PASSWORD Z_APP_DB_PASSWORD LITELLM_DB_PASSWORD Z_SOCKET_PROXY_TOKEN; do
  sed -i "s|^$k=.*|$k=$(openssl rand -hex 32)|" .env; done
for k in LITELLM_MASTER_KEY LITELLM_SALT_KEY; do sed -i "s|^$k=.*|$k=sk-$(openssl rand -hex 32)|" .env; done
sed -i "s|^Z_SECRET_KEY=.*|Z_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n')|" .env
echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock)" >> .env   # the socket's group, for z-socket-proxy only
docker build -f deploy/images/z-server/Dockerfile -t z-server:local .
docker build -f deploy/images/z-runner/Dockerfile -t z-runner:local .
echo "Z_RUNNER_IMAGE=$(docker image inspect --format '{{index .RepoDigests 0}}' z-runner:local)" >> .env   # z-runner@sha256:...
docker compose --env-file .env -f deploy/compose/docker-compose.yml up -d --wait   # z-migrate and z-seed run first
docker compose --env-file .env -f deploy/compose/docker-compose.yml logs z-server | grep bootstrap   # one-time password for the first connection (z-bootstrap)
```
- `--env-file .env` is required: Compose otherwise looks for `.env` next to the compose file, and the passwords, `DOCKER_GID` and `Z_RUNNER_IMAGE` would be missing.
- A locally built image has a `RepoDigests` entry only with Docker's containerd image store (`docker info` shows `driver-type: io.containerd.snapshotter.v1`). With the classic store, push `z-runner` to your registry and use `<registry>/z-runner@sha256:...` instead; on a laptop, `Z_ENV=development` in `.env` accepts `z-runner:local` (development only: it also allows dev sign-in, test accounts and weak secrets).
- After upgrading packs: `docker compose --env-file .env -f deploy/compose/docker-compose.yml run --rm z-seed` (idempotent).

## 2. Helm chart (`deploy/helm/z`)
Templates: one z-server Deployment running the `all` entrypoint (api, scheduler, gateway, executor, learner and monitor in one process; `server.replicas` of it), `z-ui` and `litellm` Deployments, optional `z-browser` (`browser.enabled`) and `egress-proxy` (`egressProxy.enabled`: Squid with a domain allowlist from `config.z.egress.mirrors`) Deployments; label `z.io/runner-reachable: "true"` on the z-server, LiteLLM and egress-proxy pods; Services; Ingress (class configurable, with a Traefik Middleware for the HTTPS redirect) or HTTPRoute; ConfigMaps from `config.z` and `config.litellm`; a Secret or an ExternalSecret; a PVC for artifacts; namespace `z-runners` with ResourceQuota, LimitRange, PSA labels, NetworkPolicies, the runners' ServiceAccount and the scheduler's Role/RoleBinding; NetworkPolicies of the release namespace; optional RuntimeClass; optional embedded Postgres StatefulSet; the `migrate` hook Job (with `migrate.mode: hook`) and the `seed` hook Job; PodDisruptionBudgets; optional ServiceMonitor (if Prometheus Operator is present) and Grafana dashboard ConfigMap; optional labelled release Namespace (`releaseNamespace.create`).

Values files:
- `values.yaml`: safe defaults, everything hardened.
- `values-k3s.yaml`: `ingress.className: traefik` with an HTTP→HTTPS redirect (`ingress.traefikHttpsRedirect`: a Traefik `RedirectScheme` Middleware on the Ingress router while TLS is on), `persistence.artifacts.storageClass: local-path`, `postgres.embedded: true` (migrations in an init container of z-server), one replica of each Deployment.
- `values-k8s.yaml`: `ingress.className: nginx`, `postgres.embedded: false` (DSNs in `database.existingSecret`, migrations in a pre-install/pre-upgrade hook Job), 2 replicas of the `all` z-server (artifacts on ReadWriteMany storage), 2 of z-ui and LiteLLM, PDBs on.

`env` is `production` (default), `staging` or `development`. With `production` the chart refuses to render when the network policies are switched off (`networkPolicy.enabled`, `networkPolicy.serverEgress.enabled`, `runners.networkPolicy.enabled`; schema `if`/`then` and a template `fail`) or LiteLLM egress is not pinned (below), or an image it runs (z-server, z-runner, LiteLLM, and z-ui, z-browser, egress-proxy, Postgres when enabled) is not pinned by digest.

Client addresses: z-server trusts `X-Forwarded-For` only from `server.trustedProxies` (`Z_TRUSTED_PROXIES`), by default `cluster.podCIDR` (k3s `10.42.0.0/16`), so the audit log and the per-IP sign-in limiter record the client that the ingress controller and z-ui forward rather than the z-ui pod. Runner pods share that CIDR and could forge the header on their own requests to z-server; narrow the value to the z-ui / ingress controller ranges where the CNI allows it, or set `127.0.0.1` to trust no proxy.

The release namespace must enforce Pod Security `restricted` like `z-runners` (`07-security.md` §3). Helm stores the release record in that namespace before it creates any resource, so the chart cannot create or label it on `helm install`: create and label it first instead of `--create-namespace` (NOTES.txt warns after install or upgrade when the label is missing). `releaseNamespace.create: true` renders the labelled Namespace for `helm template | kubectl apply` and Argo CD.

```bash
kubectl create namespace z
kubectl label namespace z pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/enforce-version=latest \
  pod-security.kubernetes.io/audit=restricted pod-security.kubernetes.io/warn=restricted

# k3s
helm upgrade --install z deploy/helm/z -n z \
  -f deploy/helm/z/values-k3s.yaml -f my-values.yaml
# first connection: user z-bootstrap. z-server writes the one-time password to a memory-backed file
# (Z_BOOTSTRAP_SECRET_FILE=/run/z-secrets/bootstrap), never to the log; the log line names the file and,
# with --prefix, the pod that wrote it
kubectl -n z logs -l app.kubernetes.io/instance=z,app.kubernetes.io/component=server -c server \
  --prefix --tail=-1 | grep "secret written to"
kubectl -n z exec <pod> -c server -- cat /run/z-secrets/bootstrap

# Kubernetes
helm upgrade --install z deploy/helm/z -n z \
  -f deploy/helm/z/values-k8s.yaml -f my-values.yaml
```

### gVisor on k3s
Install `runsc` and the containerd shim on nodes, add a `config.toml.tmpl` runtime entry for `runsc`, restart k3s, then:
```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata: { name: gvisor }
handler: runsc
```
and set `runners.runtimeClassName: gvisor`. Check the current k3s and gVisor docs for the exact template.

### Runner egress allowlist (rendered by the chart)
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: { name: runners-egress, namespace: z-runners }
spec:
  podSelector: {}
  policyTypes: [Ingress, Egress]
  egress:
    - to: [{ namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: z } },
             podSelector: { matchLabels: { z.io/runner-reachable: "true" } } }]   # litellm, api, gateway, egress-proxy (or the single `all` pod)
    - to: [{ namespaceSelector: {}, podSelector: { matchLabels: { k8s-app: kube-dns } } }]
      ports: [{ protocol: UDP, port: 53 }, { protocol: TCP, port: 53 }]
```

### Release namespace egress (rendered by the chart)
- **LiteLLM** (invariant 1): DNS, its database, and `networkPolicy.litellmEgress`, the inference cluster. With `env: production` the render fails unless `litellmEgress` lists at least one rule with an `ipBlock` CIDR and ports (the inferX gateway); `0.0.0.0/0` and `::/0` are refused.
- **z-server** (`networkPolicy.serverEgress`, on, cannot be disabled in production): DNS; LiteLLM, the embedded Postgres and z-browser by pod selector (not the egress proxy: z-server only hands its URL to runners and tool sandboxes); the Kubernetes API (`serverEgress.kubernetesApi`, default any address on TCP 6443, which is how k3s and kubeadm expose the API server endpoints after Service translation; managed clusters often use 443; on Cilium, `ipBlock` does not match node IPs unless `policy-cidr-match-mode=nodes`); the external Postgres (`networkPolicy.databaseEgress`, required when `postgres.embedded: false`); and `serverEgress.allow`, the only knob for everything else: the GitLab instance (OIDC issuer, GitLab connector), tool APIs, notification webhooks, audit forwarding, inference Prometheus.
- **egress-proxy**: TCP 80/443 to `egressProxy.egressTo` (default `0.0.0.0/0`); every `0.0.0.0/0` peer gets `except` for `169.254.0.0/16` (metadata), `cluster.podCIDR` and `cluster.serviceCIDR` (k3s defaults `10.42.0.0/16`, `10.43.0.0/16`) and RFC 1918. Internal mirrors are an explicit opt-in (`egressProxy.privateAllow`).

```yaml
# my-values.yaml
networkPolicy:
  litellmEgress:  [{ to: [{ ipBlock: { cidr: 10.20.0.15/32 } }], ports: [{ port: 443, protocol: TCP }] }]   # inferX gateway
  serverEgress:
    allow:        [{ to: [{ ipBlock: { cidr: 10.10.0.0/24 } }], ports: [{ port: 443, protocol: TCP }] }]  # GitLab, tools
```

## 3. Images and CI
See `13-ci-cd.md`: GitHub Actions on the GitHub repo (build, scan, sign, publish to GHCR), GitLab CI on the internal mirror (the common checks, plus signed-image deploy to the team's k3s through a protected environment). Images: `z-server`, `z-runner`, `z-browser`, `z-ui`, `z-socket-proxy`, `egress-proxy`, multi-arch, tags `vX.Y.Z` and `sha-<short>`, always deployed by digest. The LiteLLM image is pinned by digest too.

## 4. Operations
- `z-server migrate` (Alembic) runs before the new code serves, as `migrate.mode` decides (`auto` by default): a `pre-install,pre-upgrade` hook Job with an external Postgres (`values-k8s.yaml`), an init container of the z-server pod with the embedded Postgres (`values-k3s.yaml`; single replica only, the render fails otherwise). On Compose, the `z-migrate` service. Details in `deploy/helm/z/README.md` ("Migrations").
- Backups: Postgres (pg_dump or the operator), the artifacts volume, and the keys (`deploy/README.md`, "Backups and keys"). LiteLLM uses its own **database** (`litellm`) on the same Postgres instance: the init script of the Compose and embedded Postgres creates both databases and roles; with an external Postgres you create them first (SQL in `deploy/helm/z/README.md`).
- Key rotation: `LITELLM_MASTER_KEY` (rotate and restart), connector credentials through the Connectors screen. `Z_SECRET_KEY` and `LITELLM_SALT_KEY` cannot be rotated in place yet (`deploy/README.md`, "Backups and keys").
- Upgrades: chart version = app version. There is no `CHANGELOG.md`: the release notes are generated from the conventional commits since the previous tag (`ci/scripts/changelog.py`, GitLab `release:notes` job) and published with each GitLab release; read them before upgrading.
- `deploy/README.md` (written during the build) holds the step-by-step for each target.
- Per-environment site values (staging, production) are templated in `deploy/environments/`; the release process (branches, versions, migrations, rollback, hotfix) is `14-release-and-changes.md`.

## 5. Configuration keys accepted but not read
`config/z.yaml` (or Helm `config.z`) is validated against `contracts/config.schema.json`, and `config/z.example.yaml` shows every section. Some keys the schema accepts are **not read by z-server or z-runner yet**: setting them changes nothing. They stay in the schema so existing files keep validating.

| Key | Status | What decides it today |
|---|---|---|
| `memory.top_k_max` | Not read (schema only, not in the example) | The agent profile's `memory.top_k` |
| `memory.embedding_dim` | Not read from the file | The migration reads `Z_EMBEDDING_DIM` from the environment (the example substitutes the same variable) |
| `secrets.backend`, `secrets.vault.{address,mount,path_prefix}` | Not read | Each credential reference names its backend (`env:`, `file:`, `kubernetes:`, `vault:<mount>/<path>#<key>`); Vault uses `VAULT_ADDR`, `VAULT_TOKEN` or `VAULT_K8S_ROLE`, `VAULT_NAMESPACE`. There is no path prefix |
| `inference.probes.{models_poll_s,tool_call_probe_s}` | Not read | Inference metrics are queried on demand (15 s cache); the synthetic tool-call probe is not built yet |
| `notifications.{mattermost_webhook,slack_webhook,smtp_url}` | Not read | Notification channels and SMTP are set in Admin → Notifications (stored in the database) |
| `notifications.digest.*` | Not read | E-mail digests are not built yet |
| `egress.browser_targets` | Not read | Each browser connector's hosts (checked by z-browser), and Helm `browser.egressTo` for the network policy |
| `ui.default_theme`, `ui.default_font` | Not read | Present in Helm values only, used by nothing; the web app starts in `night` and each user switches the theme from the user menu or ⌘K; the font is not selectable |
