# Deploying Z

Status: Docker Compose (Postgres, LiteLLM, z-server, z-ui on :8080, docker-socket-proxy, hardened runners) and the
Helm chart for k3s and Kubernetes (`deploy/helm/z`, kubernetes executor) are ready. The egress proxy for package
mirrors and the z-browser worker are not built yet: runners reach only Z and LiteLLM.

## Docker Compose (local or single host)

```bash
cp .env.example .env                                   # fill POSTGRES_PASSWORD, Z_APP_DB_PASSWORD, Z_SECRET_KEY, LITELLM_MASTER_KEY, OIDC_*
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
- Two database roles: `z` (owner, used only by `z-migrate`) and `z_app` (the server; it cannot update or delete
  audit events). They are created by `deploy/compose/postgres/init.sh` on the first start.
- LiteLLM runs the official image pinned by digest (`v1.105.0`). It is the only component that reaches models.

### Verify without the inference cluster (test only)
`tests/e2e/compose.fakes.yml` replaces inferX by the fake LLM and adds the fake GitLab OIDC provider:

```bash
docker build --secret id=extra_ca,src=/dev/null -f tests/e2e/Dockerfile.fakes -t z-fakes:local .
docker compose --env-file .env -f deploy/compose/docker-compose.yml -f tests/e2e/compose.fakes.yml up -d --wait
```

Never use this overlay for a real deployment.

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
