# Helm chart `z`

Deploys Z on **k3s** (`values-k3s.yaml`) or **vanilla Kubernetes** (`values-k8s.yaml`) from one chart: the
differences live in values, never in templates (no k3s-only API). Spec: `docs/11-deploy.md` §2-4,
hardening: `docs/07-security.md` §3.

| Component | Kind | Notes |
|---|---|---|
| z-server | Deployment (`all` entrypoint), Service, ServiceAccount, PVC | API, scheduler, gateway, executor, learner, monitor in one process. Labelled `z.io/runner-reachable: "true"` |
| z-ui | Deployment, Service | nginx-unprivileged (uid 101), proxies `/api` to z-server; Ingress / HTTPRoute target |
| LiteLLM | Deployment, Service, ConfigMap | Official image pinned by digest, uid 65534. Labelled `z.io/runner-reachable: "true"` |
| Postgres 16 + pgvector | StatefulSet (optional) | `postgres.embedded: true` (k3s); external Postgres otherwise |
| Runners | Namespace `z-runners` | PSA `restricted`, ResourceQuota, LimitRange, ServiceAccount `z-runner` (no token), NetworkPolicies, Role for z-server |
| Migrations | hook Job or init container | `z-server migrate` with the owner role |
| Packs | post-install/post-upgrade hook Job | `z-server seed --packs /app/packs` |

## Install

Images are published by CI as `ghcr.io/k3e9x/z-{server,runner,ui,browser}` (tags `vX.Y.Z`, `sha-<short>`).
Always deploy by digest: with `env: production` (the default) the chart **refuses to render** unless every
image it runs has one (`server`, `runners`, `litellm`, and `ui`, `browser`, `egressProxy`, `postgres` when
enabled; LiteLLM and Postgres ship pinned), and z-server itself refuses a runner image without a digest.

Put the site-specific values in your own file, e.g. `my-values.yaml`:

```yaml
ingress:
  host: z.example.internal
oidc:
  issuerUrl: https://gitlab.example.internal
  clientId: <gitlab-oauth-app-id>
server:
  image: { digest: "sha256:..." }
runners:
  image: { digest: "sha256:..." }
ui:
  image: { digest: "sha256:..." }
litellm:
  inferenceApiBase: https://<inferx-endpoint>/v1
secrets:
  existingSecret: z-secrets   # Z_SECRET_KEY, LITELLM_MASTER_KEY, OIDC_CLIENT_SECRET, INFERENCE_API_KEY
networkPolicy:
  litellmEgress:              # required with env=production: the inferX gateway only
    - to: [{ ipBlock: { cidr: 10.20.0.15/32 } }]
      ports: [{ port: 443, protocol: TCP }]
  serverEgress:
    allow:                    # GitLab (sign-in, connector) and the tools of your connectors
      - to: [{ ipBlock: { cidr: 10.10.0.0/24 } }]
        ports: [{ port: 443, protocol: TCP }]
config:
  litellm:
    model_list: [...]         # the real inferX model ids (see values.yaml for the shape)
```

The release namespace must enforce Pod Security `restricted` (docs/07 §3) like `z-runners`. Helm keeps the
release record in that namespace before it creates anything, so the chart cannot create or label it on
`helm install`: create and label it first (instead of `--create-namespace`). NOTES warns after every
install or upgrade if the label is missing. For `helm template | kubectl apply` or Argo CD,
`releaseNamespace.create: true` renders the labelled Namespace.

```bash
kubectl create namespace z
kubectl label namespace z pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/enforce-version=latest \
  pod-security.kubernetes.io/audit=restricted pod-security.kubernetes.io/warn=restricted
```

### k3s

```bash
helm upgrade --install z deploy/helm/z -n z \
  -f deploy/helm/z/values-k3s.yaml -f my-values.yaml
```

Then read the one-time password of `z-bootstrap` as described in [First connection](#first-connection).

Traefik and `local-path` come with k3s, and k3s enforces NetworkPolicy with its bundled controller. The
embedded Postgres gets generated passwords (Secret `z-db`, kept on uninstall like its volume).

### Kubernetes

Create the databases and roles first (or use CloudNativePG), as a superuser:

```sql
CREATE ROLE z LOGIN PASSWORD '...';          -- owner, migrations only
CREATE ROLE z_app LOGIN PASSWORD '...';      -- the server: the migrations revoke UPDATE/DELETE on the audit tables
CREATE ROLE litellm LOGIN PASSWORD '...';
CREATE DATABASE z OWNER z;
CREATE DATABASE litellm OWNER litellm;
\c z
CREATE EXTENSION IF NOT EXISTS vector;
```

Then the DSN Secret and the release:

```bash
# namespace z created and labelled as above
kubectl -n z create secret generic z-db \
  --from-literal=DATABASE_URL='postgresql://z_app:...@pg.example.internal:5432/z' \
  --from-literal=MIGRATE_DATABASE_URL='postgresql://z:...@pg.example.internal:5432/z' \
  --from-literal=LITELLM_DATABASE_URL='postgresql://litellm:...@pg.example.internal:5432/litellm'
helm upgrade --install z deploy/helm/z -n z -f deploy/helm/z/values-k8s.yaml -f my-values.yaml
```

`values-k8s.yaml` runs two z-server replicas (the job queue, action executor and intake claim rows with
`FOR UPDATE SKIP LOCKED`), so the artifacts volume must be **ReadWriteMany**; set
`persistence.artifacts.storageClass`, or `server.replicas: 1` with `accessModes: [ReadWriteOnce]`.
NetworkPolicy needs a CNI that enforces it (Cilium, Calico, ...).

### First connection

At its first start z-server creates the bootstrap admin `z-bootstrap` and writes its one-time password
(valid 24 h) to `/run/z-secrets/bootstrap` in the server container of the pod that created it: a
memory-backed `emptyDir` (`medium: Memory`, 1 Mi) mounted only there, set through `Z_BOOTSTRAP_SECRET_FILE`.
The password never reaches the log (and so never your log stack): the log line only says where it is.

```bash
# which pod wrote it (--prefix prints [pod/<pod>/server] in front of the line)
kubectl -n z logs -l app.kubernetes.io/instance=z,app.kubernetes.io/component=server -c server \
  --prefix --tail=-1 | grep "password written to"
kubectl -n z exec <pod> -c server -- cat /run/z-secrets/bootstrap
```

Sign in at `/signin` as `z-bootstrap`, configure GitLab sign-in and the group mapping, then sign in with
GitLab as an admin: the bootstrap account is disabled. The file goes with the pod; to issue a new password:

```bash
POD=$(kubectl -n z get pod -l app.kubernetes.io/instance=z,app.kubernetes.io/component=server -o jsonpath='{.items[0].metadata.name}')
kubectl -n z exec "$POD" -c server -- z-server bootstrap --reset
kubectl -n z exec "$POD" -c server -- cat /run/z-secrets/bootstrap
```

The break-glass account (`Z_BREAKGLASS=true`, docs/12 §1) writes its one-time password and TOTP URI to
`/run/z-secrets/breakglass` (`Z_BREAKGLASS_SECRET_FILE`) in the same way.

## Migrations

| `migrate.mode` | When | How |
|---|---|---|
| `hook` | external Postgres (`auto` picks it) | Job `z-server-migrate`, `pre-install,pre-upgrade`, reads `MIGRATE_DATABASE_URL` from `database.existingSecret`, which therefore must exist before the install |
| `initContainer` | embedded Postgres (`auto` picks it) | init container of the z-server pod. The embedded Postgres is a normal release resource, so it does not exist yet when pre-install hooks run. Single replica only (the render fails otherwise) |

The seed Job is a `post-install,post-upgrade` hook with retries (on a first k3s install it may start before
the migrations ran).

## Network policies

- `z-runners`: `default-deny` (ingress and egress), then `runners-egress` allows only TCP
  `runners.networkPolicy.reachablePorts` (8000, 4000) to pods labelled `z.io/runner-reachable: "true"` in the
  release namespace (z-server, LiteLLM) and DNS to `dns.podLabels` in `dns.namespace` (CoreDNS is labelled
  `k8s-app: kube-dns` in `kube-system` on k3s and most distributions; adjust for NodeLocal DNSCache). No
  internet, no Kubernetes API, no traffic between runner pods. The egress proxy for package mirrors
  (`egress.mirrors`) will join the reachable pods when it ships; until then runners reach no mirror.
- Release namespace (ingress only): z-server from `z-runners` and z-ui (or the ingress controller when
  `ui.enabled: false`), plus `networkPolicy.metricsFrom`; LiteLLM from `z-runners` and z-server; z-ui from
  the ingress controller (`networkPolicy.ingressController`, Traefik in `kube-system` on k3s, ingress-nginx
  in `ingress-nginx` on Kubernetes); Postgres from pods labelled `z.io/postgres-client: "true"` of the release,
  and no egress.
- LiteLLM egress: DNS, the embedded Postgres (or `networkPolicy.databaseEgress`) and
  `networkPolicy.litellmEgress`, the inference cluster. With `env: production` the render **fails** unless
  `litellmEgress` has a rule with an `ipBlock` CIDR and ports (the inferX gateway), and `0.0.0.0/0` / `::/0`
  are refused there (invariant 1: LiteLLM is the only path to models and reaches nothing else).
- z-server egress (`networkPolicy.serverEgress`, on; cannot be disabled in production): DNS, LiteLLM, the
  embedded Postgres and z-browser (pod selectors, rendered by the chart; not the egress proxy, which only
  runners use), the Kubernetes API (`serverEgress.kubernetesApi`, default any address on TCP 6443: narrow it to your control-plane IPs, use
  443 on most managed clusters; on Cilium `ipBlock` does not match node IPs unless
  `policy-cidr-match-mode=nodes`), the external Postgres (`networkPolicy.databaseEgress`, required when
  `postgres.embedded: false`), and `serverEgress.allow`: GitLab (OIDC issuer and connector), the tool APIs of
  your connectors, notification webhooks, audit forwarding, `INFERENCE_PROMETHEUS_URL`. Nothing else.
- Pod Security Admission: the chart labels `z-runners` `restricted`; label the release namespace yourself
  before the install (see [Install](#install)): every chart pod satisfies `restricted`.

## gVisor

Runner pods can run under gVisor through a RuntimeClass (`runners.runtimeClassName`, passed to the executor
as `Z_RUNNER_RUNTIME_CLASS`).

- k3s: install `runsc` and `containerd-shim-runsc-v1` on every node, add a `runsc` runtime to
  `/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl` (check the current k3s and gVisor docs for the
  exact template), restart k3s.
- Kubernetes: per distribution (GKE Sandbox, or the same containerd configuration).

Then either create the RuntimeClass yourself or let the chart do it:

```yaml
runners:
  runtimeClassName: gvisor
  runtimeClass: { create: true, name: gvisor, handler: runsc }
```

## Metrics and dashboard
z-server serves Prometheus metrics on `/metrics` with the bearer token `Z_METRICS_TOKEN` from the Secret
(`metrics.serviceMonitor.enabled` renders a ServiceMonitor that sends it; allow the Prometheus namespace in
`networkPolicy.metricsFrom`). The Z values (tokens, missions, jobs, review queue, connector reachability,
undelivered notifications, docs/06 §5) come from the database, so every replica reports the same numbers.
`metrics.grafanaDashboard.enabled` renders the "Z overview" dashboard (`dashboards/z-overview.json`) as a ConfigMap
labelled for the Grafana sidecar (`grafana_dashboard: "1"`, change it in `metrics.grafanaDashboard.labels`).

## Secrets

| Secret | Keys | Source |
|---|---|---|
| `<fullname>-secrets` | `Z_SECRET_KEY`, `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY` (encrypts the inferX key set from Z, ADR 0027; never change it), `OIDC_CLIENT_SECRET` (optional), `INFERENCE_API_KEY` (optional: can be set from Z instead) | generated once and kept (`helm.sh/resource-policy: keep`), or `secrets.existingSecret`, or an ExternalSecret (`secrets.externalSecret`) |
| `<fullname>-db` | embedded: `POSTGRES_PASSWORD`, `Z_APP_DB_PASSWORD`, `LITELLM_DB_PASSWORD` | generated once and kept, or `database.existingSecret` |
| `database.existingSecret` | external: `DATABASE_URL`, `MIGRATE_DATABASE_URL`, `LITELLM_DATABASE_URL` | yours (or an ExternalSecret you manage) |

`Z_SECRET_KEY` seals data in the database: back it up with the database. Connector credentials are never in
values: reference them from the Connectors screen as `kubernetes:<namespace>/<name>#<key>` and grant read on
exactly those Secrets with `server.secretRefs`, or mount them (`server.extraVolumes`, `file:` references).

## Values

| Key | Default | Description |
|---|---|---|
| `env` | `production` | `Z_ENV`: `production`, `staging` or `development` (schema enum). Production: the server refuses dev auth, the process executor, test accounts, runner images without digest; the chart refuses to render without the checks listed under [Production checks](#production-checks) |
| `publicUrl` | `""` | `Z_PUBLIC_URL`; empty: from `ingress.host` |
| `oidc.issuerUrl`, `oidc.clientId` | `""` | GitLab OIDC (the secret is in the Secret) |
| `authMode` | `oidc` | `Z_AUTH_MODE` |
| `server.replicas` | `1` | `all` replicas; > 1 needs RWX artifacts and `migrate.mode: hook` |
| `server.image.{repository,tag,digest}` | `ghcr.io/k3e9x/z-server`, `v<appVersion>`, `""` | digest wins over tag; `latest` refused |
| `server.splitComponents` | `false` | one Deployment per component: not implemented, `true` fails the render |
| `server.trustedProxies` | `""` | `Z_TRUSTED_PROXIES` (X-Forwarded-For) |
| `server.clusterCIDRs` | `""` | `Z_CLUSTER_CIDRS` for the SSRF guard (pod + service CIDRs) |
| `server.outboundAllow` | `""` | `Z_OUTBOUND_ALLOW` |
| `server.secretsBackend` | `kubernetes` | `Z_SECRETS_BACKEND` |
| `server.secretRefs` | `[]` | `get` on named Secrets for `kubernetes:` references |
| `server.shutdownGraceSeconds` | `10` | `Z_SHUTDOWN_GRACE_S` (terminationGracePeriodSeconds = this + 20) |
| `server.extraEnv`, `server.extraEnvFrom`, `server.extraVolumes`, `server.extraVolumeMounts` | `[]` | connector URLs, `env:`/`file:` secret references |
| `migrate.mode` | `auto` | `auto`, `hook`, `initContainer` (see Migrations) |
| `seed.enabled`, `seed.packs`, `seed.only` | `true`, `/app/packs`, `[]` | pack seeding hook |
| `runners.namespace` | `z-runners` | `Z_RUNNER_NAMESPACE` |
| `runners.createNamespace` | `true` | create the namespace with PSA labels |
| `runners.serviceAccountName` | `z-runner` | `Z_RUNNER_SERVICE_ACCOUNT` (no token) |
| `runners.image.{repository,tag,digest}` | `ghcr.io/k3e9x/z-runner` | `Z_RUNNER_IMAGE`; digest required in production |
| `runners.runtimeClassName` | `""` | `Z_RUNNER_RUNTIME_CLASS` (gVisor) |
| `runners.runtimeClass.create` | `false` | create the RuntimeClass (`name`, `handler`) |
| `runners.jobTTLSeconds` | `3600` | `Z_RUNNER_JOB_TTL_S` |
| `runners.podSecurity.level` | `restricted` | the only accepted value |
| `runners.resourceQuota`, `runners.limitRange` | on | quota of the runner namespace, container defaults/max |
| `runners.networkPolicy.enabled` | `true` | runner namespace policies; refused `false` in production |
| `runners.networkPolicy.reachablePorts` | `[8000, 4000]` | ports runners may open on reachable pods |
| `runners.networkPolicy.extraEgress` | `[]` | extra egress rules for runners |
| `dns.namespace`, `dns.podLabels` | `kube-system`, `k8s-app: kube-dns` | DNS peer of the runner policy |
| `ui.enabled`, `ui.replicas`, `ui.image` | `true`, `1`, `ghcr.io/k3e9x/z-ui` | web app |
| `litellm.image` | `docker.io/litellm/litellm:v1.105.0@sha256:6094...` | same reference as compose; never 1.82.7/1.82.8 |
| `litellm.inferenceApiBase` | `""` | `INFERENCE_API_BASE` |
| `postgres.embedded` | `false` | embedded StatefulSet (`true` in k3s values) |
| `postgres.storage.{size,storageClass}` | `20Gi`, `""` | data volume |
| `database.existingSecret` | `""` | DSNs (external, required) or passwords (embedded) |
| `database.appRole` | `z_app` | `Z_DB_APP_ROLE` |
| `database.embeddingDim` | `1024` | `Z_EMBEDDING_DIM` |
| `secrets.existingSecret` | `""` | Secret with `Z_SECRET_KEY`, `LITELLM_MASTER_KEY`, ... |
| `secrets.externalSecret.enabled` | `false` | render an ExternalSecret (`external-secrets.io/v1`) |
| `persistence.artifacts.{enabled,size,storageClass,accessModes,existingClaim}` | `true`, `20Gi`, `""`, `[ReadWriteOnce]`, `""` | `Z_ARTIFACTS_DIR` volume |
| `ingress.{enabled,className,host,tls}` | `true`, `""`, `z.example.internal`, TLS on | Ingress to z-ui |
| `httpRoute.{enabled,parentRefs,hostnames}` | `false` | Gateway API HTTPRoute |
| `networkPolicy.enabled` | `true` | release namespace policies; refused `false` in production |
| `networkPolicy.ingressController` | `kube-system` | namespace/pod selector of the ingress controller |
| `networkPolicy.metricsFrom` | `[]` | peers allowed to scrape `/metrics` |
| `networkPolicy.litellmEgress` | `[]` | restrict LiteLLM egress to these rules (+ DNS); required in production |
| `networkPolicy.databaseEgress` | `[]` | egress to the external Postgres (z-server, LiteLLM); required with `postgres.embedded: false` |
| `networkPolicy.serverEgress.enabled` | `true` | z-server egress policy; refused `false` in production |
| `networkPolicy.serverEgress.kubernetesApi` | any address, TCP 6443 | egress rules to the API server endpoints |
| `networkPolicy.serverEgress.allow` | `[]` | GitLab, tool APIs, webhooks, audit forwarding |
| `podDisruptionBudget.enabled` | `false` | PDBs (`true` in k8s values) |
| `metrics.serviceMonitor.enabled` | `false` | rendered only if `monitoring.coreos.com/v1` is served |
| `config.z` | see values.yaml | `config/z.yaml` (validated by the server against `contracts/config.schema.json`) |
| `config.litellm` | see values.yaml | `config/litellm.yaml` |

`podSecurityContext` and `containerSecurityContext` apply to every pod and container; the schema refuses any
weakening (invariant 13).

## Production checks

With `env: production` (the default) `helm template` / `helm install` fail, with the reason, unless:

- `networkPolicy.enabled`, `runners.networkPolicy.enabled` and `networkPolicy.serverEgress.enabled` are
  `true` (the schema refuses `false`, and the templates refuse it again under `--skip-schema-validation`);
- `networkPolicy.litellmEgress` pins LiteLLM to the inference cluster (a CIDR and ports, not `0.0.0.0/0`);
- every image of the release is pinned by digest (`<component>.image.digest`).

`ci/render-values.yaml` (not packaged) satisfies these with placeholders so CI can lint and render the
production defaults: `-f values-k3s.yaml -f ci/render-values.yaml`. Never deploy with it.

In every environment `env` must be `production`, `staging` or `development`.

## Checks

```bash
helm lint deploy/helm/z -f deploy/helm/z/values-k3s.yaml -f deploy/helm/z/ci/render-values.yaml
helm template z deploy/helm/z -n z -f deploy/helm/z/values-k3s.yaml -f deploy/helm/z/ci/render-values.yaml \
  | kubeconform -strict -summary -ignore-missing-schemas
```

Same for `values-k8s.yaml`; `.github/workflows/helm.yml` runs these plus the ServiceMonitor path and the
refused combinations, with pinned and verified helm and kubeconform.
