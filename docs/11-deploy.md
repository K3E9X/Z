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
Services: `postgres` (16 + pgvector, init script creating databases `z` and `litellm`), `litellm`, `z-server` (`all` entrypoint), `z-browser` (gateway-side browser worker), `egress-proxy` (allows only `egress.mirrors`), `z-ui`, `docker-socket-proxy`, optional `langfuse`.
Networks: `edge` (ui ↔ server), `core` (server ↔ postgres, litellm), `runners` (declared with `name: z_runners`, `internal: true`: runners ↔ litellm, server internal API + gateway, egress-proxy; no internet), `egress` (egress-proxy and z-browser only, to mirrors and allowlisted apps).
Runners are started by z-server through the socket proxy on the `runners` network with the hardening options of `07-security.md`.
```bash
cp .env.example .env
cp config/litellm.example.yaml config/litellm.yaml
cp config/z.example.yaml config/z.yaml
docker compose -f deploy/compose/docker-compose.yml up -d
docker compose -f deploy/compose/docker-compose.yml exec z-server z-server migrate
docker compose -f deploy/compose/docker-compose.yml exec z-server z-server seed --packs packs/
docker compose -f deploy/compose/docker-compose.yml logs z-server | grep bootstrap   # one-time password for the first connection (z-bootstrap)
```

## 2. Helm chart (`deploy/helm/z`)
Templates: Deployments for `api`, `scheduler`, `gateway`, `executor`, `learner`, `monitor` (each can be disabled to run `all` in one pod), `ui`, `litellm`, `z-browser`, `egress-proxy` (e.g. Squid or Envoy with a domain allowlist from `egress.mirrors`); label `z.io/runner-reachable: "true"` on litellm, api, gateway and egress-proxy (or the `all` pod); Services; Ingress (class configurable) or HTTPRoute; ConfigMaps from `config/z.yaml` and `config/litellm.yaml`; Secrets or ExternalSecrets; PVC for artifacts (or S3 settings); namespace `z-runners` with ResourceQuota, LimitRange, PSA labels, NetworkPolicies; scheduler ServiceAccount + Role/RoleBinding in `z-runners`; optional RuntimeClass reference; optional Postgres StatefulSet for dev; PodDisruptionBudgets; ServiceMonitor (if Prometheus Operator is present).

Values files:
- `values.yaml`: safe defaults, everything hardened.
- `values-k3s.yaml`: `ingress.className: traefik` with an HTTP→HTTPS redirect (`ingress.traefikHttpsRedirect`: a Traefik `RedirectScheme` Middleware on the Ingress router while TLS is on), `persistence.storageClass: local-path`, `postgres.embedded: true`, single replica per component.
- `values-k8s.yaml`: `ingress.className: nginx`, `postgres.embedded: false` (external DSN), 2 replicas for api and gateway, PDBs on.

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
  --prefix --tail=-1 | grep "password written to"
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
See `13-ci-cd.md`: GitHub Actions on the GitHub repo (build, scan, sign, publish to GHCR), GitLab CI on the internal mirror (same checks, plus signed-image deploy to the team's k3s through a protected environment). Images: `z-server`, `z-runner`, `z-browser`, `z-ui`, multi-arch, tags `vX.Y.Z` and `sha-<short>`, always deployed by digest. The LiteLLM image is pinned by digest too.

## 4. Operations
- `z-server migrate` (Alembic) runs as a Helm pre-upgrade Job.
- Backups: Postgres (pg_dump or the operator), artifacts volume/S3. LiteLLM uses its own **database** (`litellm`) on the same Postgres instance; an init script / Helm hook creates both databases.
- Key rotation: `LITELLM_MASTER_KEY`, `Z_SECRET_KEY` (job tokens are short-lived; rotate and restart), connector credentials through the Connectors screen.
- Upgrades: chart version = app version; read `CHANGELOG.md`.
- `deploy/README.md` (written during the build) holds the step-by-step for each target.
- Per-environment site values (staging, production) are templated in `deploy/environments/`; the release process (branches, versions, migrations, rollback, hotfix) is `14-release-and-changes.md`.
