# Deploying Z

Status: Docker Compose (Postgres, LiteLLM, z-server, z-ui on :8080, docker-socket-proxy, hardened runners) and the
Helm chart for k3s and Kubernetes (`deploy/helm/z`, kubernetes executor) are ready, with two optional parts:
the egress proxy (runners reach package mirrors only through it) and the z-browser worker (browser connectors).

## Docker Compose (local or single host)

```bash
cp .env.example .env                                   # then fill every secret (below) and OIDC_*
for k in POSTGRES_PASSWORD Z_APP_DB_PASSWORD LITELLM_DB_PASSWORD LITELLM_MASTER_KEY LITELLM_SALT_KEY Z_SOCKET_PROXY_TOKEN; do
  sed -i "s|^$k=.*|$k=$(openssl rand -hex 32)|" .env; done
sed -i "s|^Z_SECRET_KEY=.*|Z_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n')|" .env
echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock)" >> .env   # the socket's group, for z-socket-proxy only
cp config/litellm.example.yaml config/litellm.yaml     # put the real inferX model ids
cp config/z.example.yaml config/z.yaml
docker build -f deploy/images/z-server/Dockerfile -t z-server:local .
docker compose --env-file .env -f deploy/compose/docker-compose.yml up -d --wait
docker compose --env-file .env -f deploy/compose/docker-compose.yml logs z-server | grep bootstrap   # one-time password
curl -s http://127.0.0.1:8000/readyz   # z-server; the web app is http://127.0.0.1:8080 (z-ui)
```

- Behind a TLS-intercepting proxy, pass its CA at build time only:
  `docker build --secret id=extra_ca,src=/path/ca.pem ...` (or `Z_EXTRA_CA=/path/ca.pem` for `compose build`).
- Internal package mirror: `--build-arg PIP_INDEX_URL=... --build-arg UV_INDEX_URL=...`.
- Three database roles: `z` (owner, used only by `z-migrate`), `z_app` (the server; it cannot update or delete
  audit events) and `litellm` (owns the LiteLLM database only). They are created by `deploy/compose/postgres/init.sh`
  on the first start.
- `Z_ENV` defaults to `production`: weak or example secrets, dev sign-in, test accounts and runner images without
  a digest are refused. Set `Z_ENV=development` only on a laptop.
- Each secret reaches only the containers that need it: z-server and z-seed read `.env` for connector credentials,
  with the database owner password, the LiteLLM salt key and the model provider keys blanked out.
- Networks: Postgres, the Docker socket proxy and z-browser are on internal networks reachable by z-server only;
  runners are on an internal network with z-server, LiteLLM and the egress proxy. Every service has memory, CPU and
  process limits, a read-only root and no capability.
- Before z-server starts, `z-migrate` applies the migrations and `z-seed` loads the packs of the image into the
  catalog (idempotent: run it again after upgrading packs with `docker compose run --rm z-seed`).
- LiteLLM runs the official image pinned by digest (`v1.105.0`). It is the only component that reaches models.

### Optional: package mirrors and browser connectors
```bash
# runners may install packages, only from these mirrors (squid allowlist)
Z_EGRESS_MIRRORS=nexus.example.internal EGRESS_PROXY_URL=http://egress-proxy:3128 \
  docker compose --env-file .env -f deploy/compose/docker-compose.yml --profile egress up -d
# browser connectors: set Z_BROWSER_URL=http://z-browser:8090 and Z_BROWSER_TOKEN (>= 16 chars) in .env
docker compose --env-file .env -f deploy/compose/docker-compose.yml --profile browser up -d
```
On Helm: `egressProxy.enabled` (with `config.z.egress.mirrors`) and `browser.enabled` (with `browser.egressTo`,
the networks of the web apps your browser connectors open; nothing else is reachable).

### Verify without the inference cluster (test only)
`tests/e2e/compose.fakes.yml` replaces inferX by the fake LLM and adds the fake GitLab OIDC provider:

```bash
docker build --secret id=extra_ca,src=/dev/null -f tests/e2e/Dockerfile.fakes -t z-fakes:local .
docker compose --env-file .env -f deploy/compose/docker-compose.yml -f tests/e2e/compose.fakes.yml up -d --wait
```

Never use this overlay for a real deployment.

### Backups and keys
Back up together, and keep them apart from the server: the `pgdata` volume (`docker compose exec postgres pg_dumpall
-U z > z.sql` daily, kept encrypted), `Z_SECRET_KEY` (it seals stored secrets and signs audit checkpoints: without it
the sealed OIDC client secret, refresh tokens and TOTP seeds cannot be read and checkpoints cannot be verified) and
`LITELLM_SALT_KEY` (it encrypts the inferX key LiteLLM stores). Neither key can be rotated in place yet: changing
one means setting the inferX key, the OIDC secret and personal connections again.

## Helm (k3s and Kubernetes)

One chart, `deploy/helm/z`, for both targets; the differences are in `values-k3s.yaml` and `values-k8s.yaml`.
Details (values, migrations, network policies, gVisor, secrets): `deploy/helm/z/README.md`.

1. Write `my-values.yaml`: `ingress.host`, `oidc.issuerUrl` / `oidc.clientId`, the image digests
   (`server.image.digest`, `runners.image.digest`, `ui.image.digest`; z-server refuses to start in production
   without a runner digest), `litellm.inferenceApiBase`, `config.litellm.model_list` with the real inferX model
   ids, and the secrets (`secrets.existingSecret`, or let the chart generate `Z_SECRET_KEY` and
   `LITELLM_MASTER_KEY` and pass `secrets.oidcClientSecret` / `secrets.inferenceApiKey`).
2. Install:

```bash
# k3s: Traefik, local-path, embedded Postgres (passwords generated and kept), migrations in an init container
helm upgrade --install z deploy/helm/z -n z --create-namespace \
  -f deploy/helm/z/values-k3s.yaml -f my-values.yaml

# Kubernetes: ingress-nginx, external Postgres. Create the roles z / z_app / litellm, the databases z and
# litellm and the vector extension first (SQL in deploy/helm/z/README.md), then the DSN Secret:
kubectl create namespace z
kubectl -n z create secret generic z-db \
  --from-literal=DATABASE_URL='postgresql://z_app:...@pg:5432/z' \
  --from-literal=MIGRATE_DATABASE_URL='postgresql://z:...@pg:5432/z' \
  --from-literal=LITELLM_DATABASE_URL='postgresql://litellm:...@pg:5432/litellm'
helm upgrade --install z deploy/helm/z -n z -f deploy/helm/z/values-k8s.yaml -f my-values.yaml
```

3. One-time password of the bootstrap admin, printed by z-server at its first start:
   `kubectl -n z logs deploy/z-server | grep bootstrap`. Then follow "First connection" below
   (re-enable: `kubectl -n z exec deploy/z-server -- z-server bootstrap --reset`).

- Runner pods start in the namespace `z-runners` (Pod Security `restricted`, quota, default-deny network
  policies: runners reach only z-server, LiteLLM and DNS). NetworkPolicy is enforced by k3s out of the box; on
  Kubernetes it needs a CNI that enforces it.
- `values-k8s.yaml` runs two z-server replicas: the artifacts volume must be ReadWriteMany.
- Checks run in CI (`.github/workflows/helm.yml`): `helm lint`, `helm template | kubeconform -strict` for both
  values files.

## First connection
1. Sign in at `/signin` with `z-bootstrap` and the one-time password from the logs (valid 24 h, shown once).
2. Admin → Sign-in: GitLab issuer URL, client id and secret (the GitLab OAuth app, scopes `openid profile email`,
   redirect URI `https://<z-host>/api/v1/auth/callback` and `http://127.0.0.1:8765/callback` for the CLI fallback).
   "Test" checks discovery and JWKS.
3. Admin → Group mapping: map your GitLab groups to roles; run the dry run; save.
4. Sign in with GitLab as a user mapped to `admin`: the bootstrap account is disabled automatically.
   To re-enable it: `docker compose ... exec z-server z-server bootstrap --reset`.

## Audit log operations
- **Forwarding** (Admin → Audit): syslog RFC 5424 over TLS (`tls://host:6514`) or JSON lines over HTTPS with an
  optional bearer token given as a secret reference. Delivery is at-least-once and in order; the tab shows the
  last delivered event and the last error. An internal CA is trusted through `SSL_CERT_FILE` (ADR 0021).
- **Retention.** The app's database role cannot delete audit rows. Purge with the owner role, only up to a signed
  checkpoint older than `retention_days`, so `verify` still anchors the remaining chain on that checkpoint:

  ```sql
  -- as the database owner (not z_app); keep audit_checkpoints
  BEGIN;
  ALTER TABLE audit_events DISABLE TRIGGER audit_events_append_only;
  DELETE FROM audit_events WHERE seq <= (
    SELECT max(seq) FROM audit_checkpoints WHERE ts < now() - interval '365 days');
  ALTER TABLE audit_events ENABLE TRIGGER audit_events_append_only;
  COMMIT;
  ```

  Export the range first (`GET /audit/export`) if your policy keeps an archive.
