# Deploying Z

Status of this build: **step 1 (base)**. The compose file runs Postgres, LiteLLM and z-server
(API, sign-in, admin, audit). Runners, the web UI, the egress proxy and the Helm chart arrive with the next steps.

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

## First connection
1. Sign in at `/signin` with `z-bootstrap` and the one-time password from the logs (valid 24 h, shown once).
2. Admin → Sign-in: GitLab issuer URL, client id and secret (the GitLab OAuth app, scopes `openid profile email`,
   redirect URI `https://<z-host>/api/v1/auth/callback` and `http://127.0.0.1:8765/callback` for the CLI fallback).
   "Test" checks discovery and JWKS.
3. Admin → Group mapping: map your GitLab groups to roles; run the dry run; save.
4. Sign in with GitLab as a user mapped to `admin`: the bootstrap account is disabled automatically.
   To re-enable it: `docker compose ... exec z-server z-server bootstrap --reset`.
