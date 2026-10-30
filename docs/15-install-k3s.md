# 15 — Install Z on k3s, step by step

From an empty k3s node to a first mission. Every command is meant to be run as written; values in `<angle
brackets>` are yours. The chart reference is `deploy/helm/z/README.md`, the environment values templates are in
`deploy/environments/`, and `docs/14-release-and-changes.md` covers upgrades and rollbacks once you run.

Install **staging first** (release `z` in namespace `z-staging`), run the checks of §11 there, then do the same
for production (namespace `z`). The steps are identical; only the values file differs.

## 0. What you need

| Item | Example | Who provides it |
|---|---|---|
| k3s ≥ 1.30 with the bundled Traefik, `local-path` storage and NetworkPolicy controller (the defaults) | 1 node, 8 vCPU, 32 GB RAM, 200 GB SSD for a pilot; 3 nodes for ~32 parallel jobs | Platform team |
| A DNS name pointing at the node (or the load balancer) | `z.<your-domain>` | Network team |
| A TLS certificate for that name | cert-manager `Certificate`, or a Secret `z-tls` from your PKI | PKI team |
| GitLab (self-managed or gitlab.com) with an OAuth application and groups | `cyber/z/z-admins`, `cyber/z/z-approvers`… | GitLab admin |
| An inferX project key, the private gateway URL and the model ids | `https://private.inferx…/inference/v1` | inferX owner |
| Read and write service accounts for each tool you connect | `svc-z-read`, `svc-z-write` in Jira, GitLab… | Tool owners |
| Access to the Z images | `ghcr.io/k3e9x/z-*` (a pull token), or your own registry mirror | You |
| `kubectl` and `helm` ≥ 3.14 on your workstation, with the cluster's kubeconfig | | You |

Network flows to open (everything else stays closed; the chart's NetworkPolicies enforce it inside the cluster):

| From | To | Port |
|---|---|---|
| Users | Traefik (the Z host) | 443 |
| LiteLLM pod | inferX private gateway | 443 |
| z-server pod | GitLab (sign-in and API), Jira, other tools, your SIEM (audit forwarding) | 443 (6514 for syslog/TLS) |
| Nodes | Image registry | 443 |

Runner pods have no outbound access except z-server, LiteLLM and DNS (and the egress proxy if you enable it for
package mirrors).

## 1. Check the cluster

```bash
kubectl get nodes -o wide
kubectl -n kube-system get deploy traefik
kubectl get storageclass local-path
kubectl version | grep Server        # >= v1.30
```

## 2. GitLab: the sign-in application and the groups

1. In GitLab, **Admin → Applications** (or a group's **Settings → Applications**), create an application:
   - name `Z`, **confidential**, scopes `openid profile email`;
   - redirect URIs: `https://<z-host>/api/v1/auth/callback` and `http://127.0.0.1:8765/callback` (the CLI);
   - keep the **Application ID** and the **Secret**.
2. Create the groups Z maps to roles (change the paths in `config.z.auth.group_mapping` if yours differ):
   `cyber/z/z-admins` (admin), `cyber/z/z-auditors` (auditor), `cyber/z/z-builders` (builder),
   `cyber/z/z-approvers` (approver), `cyber/z/z-operators` (operator). Teams come from
   `config.z.auth.team_mapping` (for example `cyber/soc-csirt` → team `Cyber Ops`). Members of
   `allowed_groups` with no mapped group get `viewer`.

## 3. Images

The CI publishes every image of `main` to `ghcr.io/k3e9x/<image>` with the tag `sha-<short commit>`, signs it
and attaches an SBOM. Always deploy **by digest**.

```bash
TAG=sha-$(git rev-parse --short=7 origin/main)      # or a release tag vX.Y.Z
for i in z-server z-runner z-ui z-egress-proxy z-browser; do
  echo "$i $(docker buildx imagetools inspect ghcr.io/k3e9x/$i:$TAG --format '{{json .Manifest.Digest}}')"
done
```

The repository is private, so the packages are too. Either:
- **pull from ghcr**: create a GitHub token with `read:packages` only, then the pull Secret in both namespaces
  (step 4) and `imagePullSecrets: [{name: ghcr-pull}]` in your values; or
- **mirror** the images into your registry (Harbor, GitLab registry) by digest, for example
  `crane copy ghcr.io/k3e9x/z-server@sha256:… <registry>/z/z-server@sha256:…`, and set each `image.repository`
  to your registry. The digests stay the same.

Optional: verify the signature before deploying:

```bash
cosign verify ghcr.io/k3e9x/z-server@sha256:<digest> --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github.com/K3E9X/Z/\.github/workflows/images\.yml@refs/(heads/main|tags/v[0-9]+\.[0-9]+\.[0-9]+)$'
```

## 4. Namespaces and the registry Secret

```bash
NS=z-staging      # z for production
kubectl create namespace $NS
kubectl label namespace $NS pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/enforce-version=latest
# The chart creates the runners namespace (z-runners) itself, labelled restricted. With ghcr, create it now so
# the pull Secret can live there too:
kubectl create namespace z-runners
kubectl label namespace z-runners pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/enforce-version=latest
for n in $NS z-runners; do
  kubectl -n $n create secret docker-registry ghcr-pull --docker-server=ghcr.io \
    --docker-username=<github-user> --docker-password="$(cat ~/ghcr-read-token)"
done
```

If the chart refuses to own an existing `z-runners` namespace, set `runners.createNamespace: false` (it then only
creates what goes inside). Staging and production on the same cluster need two runners namespaces: set
`runners.namespace: z-runners-staging` for staging.

## 5. Platform secrets

Create them yourself (recommended: you keep them in your vault and can restore them), never in a values file in
git:

```bash
kubectl -n $NS create secret generic z-secrets \
  --from-literal=Z_SECRET_KEY="$(openssl rand -base64 48 | tr -d '\n')" \
  --from-literal=LITELLM_MASTER_KEY="sk-$(openssl rand -hex 32)" \
  --from-literal=LITELLM_SALT_KEY="sk-$(openssl rand -hex 32)" \
  --from-literal=Z_METRICS_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=OIDC_CLIENT_SECRET="$(cat ~/gitlab-app-secret)" \
  --from-literal=INFERENCE_API_KEY="$(cat ~/inferx-key)"     # optional: it can be set later in the web app
```

Copy these values into your vault now. `Z_SECRET_KEY` seals stored secrets and signs audit checkpoints;
`LITELLM_SALT_KEY` encrypts the inferX key LiteLLM keeps. **Never change either**, and back them up with the
database (§12). With External Secrets Operator, use `secrets.externalSecret` instead (`deploy/helm/z/README.md`).

## 6. Tool credentials

One read and one write account per tool; the read one is used by agents through the gateway, the write one only
by the executor after a human approval. Two ways, which you can mix:

**a. A Kubernetes Secret, loaded as environment variables** (names from `.env.example` and the pack manifests):

```bash
kubectl -n $NS create secret generic z-connectors \
  --from-literal=GITLAB_URL=https://<gitlab> \
  --from-literal=GITLAB_TOKEN_RO=… --from-literal=GITLAB_TOKEN_RW=… \
  --from-literal=JIRA_URL=https://<site>.atlassian.net \
  --from-literal=JIRA_READ_EMAIL=svc-z-read@… --from-literal=JIRA_READ_TOKEN=… \
  --from-literal=JIRA_WRITE_EMAIL=svc-z-write@… --from-literal=JIRA_WRITE_TOKEN=… \
  --from-literal=RECORDEDFUTURE_TOKEN=…
```

and in the values: `server.extraEnvFrom: [{secretRef: {name: z-connectors}}]`.

**b. Typed in the web app**: Connectors → a connector → Credentials → *Type a secret* (needs
`connectors:manage` and a fresh sign-in). The value is sealed with `Z_SECRET_KEY`, never shown again, and
replaces the manifest reference until you choose *Use the manifest reference again*.

GitLab: prefer group or project access tokens (bot users) with an expiry, `read_api` for read and `api` for
write, limited to the groups Z works on. Microsoft 365 and Google use each person's own delegated grant (they
connect their account in Settings → Connections); only the two OAuth client secrets are platform secrets.

## 7. Your values file

`csirtlab-staging.values.yaml` (no secret in it; keep it in a private repository or a protected CI variable):

```yaml
env: staging                          # production for the production release
ingress:
  host: z-staging.<your-domain>
  tls: { enabled: true, secretName: z-tls }
oidc:
  issuerUrl: https://<gitlab>
  clientId: <gitlab-application-id>
secrets:
  existingSecret: z-secrets
imagePullSecrets: [{ name: ghcr-pull }]   # omit when you mirror into a registry the nodes can read anonymously
server:
  image: { digest: "sha256:<z-server>" }
  extraEnvFrom: [{ secretRef: { name: z-connectors } }]
runners:
  image: { digest: "sha256:<z-runner>" }
ui:
  image: { digest: "sha256:<z-ui>" }
litellm:
  inferenceApiBase: https://<inferX private gateway>/inference/v1
networkPolicy:
  litellmEgress:                       # the inferX gateway only
    - to: [{ ipBlock: { cidr: <inferX gateway IP>/32 } }]
      ports: [{ port: 443, protocol: TCP }]
  serverEgress:
    allow:                             # GitLab, Jira, your tools, the SIEM
      - to: [{ ipBlock: { cidr: <tools CIDR> } }]
        ports: [{ port: 443, protocol: TCP }]
config:
  z:
    workspace: { name: CSIRT Lab, environment: staging }
    # auth.group_mapping / team_mapping: copy them from deploy/helm/z/values.yaml and adapt the group paths
  litellm:
    model_list: []                     # copy the four entries from deploy/helm/z/values.yaml and set the real
                                       # inferX model ids (z-heavy, z-fast, z-judge, z-embed)
```

`config.z` and `config.litellm` replace whole lists: when you change one entry of `group_mapping` or
`model_list`, copy the full list from `deploy/helm/z/values.yaml`. Every key is explained in
`deploy/helm/z/values.yaml`; `deploy/environments/production.values.example.yaml` is the production template.

Check the render before installing:

```bash
helm template z deploy/helm/z -n $NS -f deploy/helm/z/values-k3s.yaml -f csirtlab-staging.values.yaml > /tmp/z.yaml
grep -c "kind:" /tmp/z.yaml        # renders without error
```

The chart refuses to render a production release without network policies, with LiteLLM egress unpinned or with
a runner image without digest: read the error, it names the value to set.

## 8. Install

```bash
helm upgrade --install z deploy/helm/z -n $NS \
  -f deploy/helm/z/values-k3s.yaml -f csirtlab-staging.values.yaml --wait --timeout 10m
kubectl -n $NS get pods
kubectl -n z-runners get sa,quota,networkpolicy
```

Expected: `z-server`, `z-ui`, `z-litellm`, `z-postgres` running (the database migration runs in an init
container), the seed Job completed. A pod in `ImagePullBackOff` means the pull Secret or the digest is wrong;
`CrashLoopBackOff` of z-server prints the reason in its log (`kubectl -n $NS logs deploy/z-server -c server`):
it checks every production setting at start and exits with the one that is missing.

## 9. First connection

```bash
POD=$(kubectl -n $NS get pod -l app.kubernetes.io/component=server -o name | head -1)
kubectl -n $NS logs $POD -c server | grep "secret written to"
kubectl -n $NS exec $POD -c server -- cat /run/z-secrets/bootstrap
```

1. Open `https://<z-host>/signin`, sign in as `z-bootstrap` with that one-time password (24 h).
2. **Admin → Sign-in**: check the GitLab issuer and client id, *Test*, save. **Admin → Group mapping**: dry run
   with a real user, save.
3. Sign out and sign in with GitLab as a member of `cyber/z/z-admins`. The bootstrap account is disabled
   automatically at that moment.

## 10. Models and tools

1. **Inference → Model providers → inferX key**: paste the key if it is not in `z-secrets` (it goes to LiteLLM
   only). The check must turn green; the Inference screen shows the four aliases healthy.
2. **Connectors**: each pack connector arrives `pending_review` with no operation exposed. Open each one, check
   its credentials (environment reference or typed secret), *Test*, then review the exposure: expose the read
   operations you need and the writes you accept. Destructive operations (for example `logos.accounts.disable`)
   ship unexposed and always need two approvers once exposed.
3. **Admin → Audit**: set the syslog/TLS or HTTPS forwarding to your SIEM and check that delivery runs.
4. **Admin → Notifications**: SMTP and the chat webhooks you want; users choose personal e-mail and digests in
   Settings → Notifications.

## 11. Smoke test

1. **New mission** → playbook *Summarize file* (pack `demo`), upload a text file, start: the mission must reach
   `succeeded` and show the report.
2. A read-only playbook on a test project (for example *Weekly vuln triage* on a test DefectDojo product):
   follow it live, check the judge and tool events, and that nothing is written.
3. A write on a test Jira project: the proposal appears in Review, you approve it, the executor creates the
   issue once; *Retry* does not create a second one.
4. CLI from your workstation: `uv tool install ./cli --with ./packages/z-common`, `z login --url https://<z-host>`,
   `z top`.
5. Admin → Platform shows the version and health; `curl -H "Authorization: Bearer <Z_METRICS_TOKEN>"
   https://<z-host>/metrics` answers (or scrape it from Prometheus; `metrics.grafanaDashboard.enabled: true` adds the "Z overview" dashboard for a
   kube-prometheus-stack Grafana).

Record the results; `docs/ACCEPTANCE.md` lists the criteria still to be proven on a real install.

## 12. Run it

- **Backups**: the embedded Postgres keeps its data in the `local-path` volume of `z-postgres`. Dump it daily and
  keep the dumps encrypted, away from the node, together with `Z_SECRET_KEY` and `LITELLM_SALT_KEY`:
  `kubectl -n $NS exec statefulset/z-postgres -- pg_dumpall -U z | gzip > z-$(date +%F).sql.gz`. Test a restore on
  staging. For more than one node, move to an external Postgres (`values-k8s.yaml` shows how).
- **Upgrades**: new digests in the values file, `helm upgrade` as in §8; migrations run before the new server
  starts and are expand-only, so `helm rollback z <revision> -n $NS` works (`docs/14`).
- **Maintenance**: Admin → Platform → Maintenance mode stops new missions and jobs (running jobs finish) and shows
  a banner to everyone; turn it on before an upgrade or a database restore.
- **Monitoring**: `/metrics` (tokens, missions, jobs, pending review, connector health, notification backlog),
  the "Z overview" Grafana dashboard (`metrics.grafanaDashboard.enabled`), and the audit forwarding to the SIEM.
- **Rotation**: tool tokens before their expiry (Connectors shows "rotated X ago"), the inferX key from the web
  app, the GitLab application secret in `z-secrets` then `kubectl -n $NS rollout restart deploy/z-server`.

## 13. Troubleshooting

| Symptom | Look at |
|---|---|
| `ImagePullBackOff` on a runner Job | the `ghcr-pull` Secret exists in `z-runners` and `imagePullSecrets` is set; the runner digest |
| z-server restarts | `kubectl logs deploy/z-server -c server`: the first lines name the missing or refused setting |
| Sign-in loops back | GitLab redirect URI exactly `https://<z-host>/api/v1/auth/callback`; the user is in `allowed_groups` |
| Missions stay `queued` | maintenance mode; a spent budget (Inference → Budgets); `kubectl -n z-runners get jobs,pods`; the quota of `z-runners` |
| Inference red | LiteLLM egress NetworkPolicy (inferX IP and port), the key check in Inference, `kubectl logs deploy/z-litellm` |
| A connector call is denied | the operation is not exposed, or its credential is missing (Connectors → Test) |
| Nothing reaches the SIEM | Admin → Audit delivery status; z-server egress to the SIEM port |
