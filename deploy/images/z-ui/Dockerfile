# z-ui: the static web app served by an unprivileged nginx, which also proxies /api to z-server.
# Non-root (uid 101), read-only root filesystem friendly (writes only to /tmp).
ARG NODE_IMAGE=node:22.23.3-bookworm-slim@sha256:3ed98443660ff48413b5af19ecefce3b9d50845a901a43420d47cc63a9fddbe9
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8

FROM ${NODE_IMAGE} AS build
# Optional internal npm mirror (docs/13 §3) and a CA for TLS-intercepting proxies as a BuildKit secret:
#   docker build --secret id=extra_ca,src=/path/ca.pem --build-arg NPM_REGISTRY=https://npm.example.internal/ ...
ARG NPM_REGISTRY=""
ENV PNPM_HOME=/pnpm CI=true
WORKDIR /src/ui
COPY ui/package.json ui/pnpm-lock.yaml ui/pnpm-workspace.yaml ./
RUN --mount=type=secret,id=extra_ca \
    set -e; \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    if [ -n "$NPM_REGISTRY" ]; then export COREPACK_NPM_REGISTRY="$NPM_REGISTRY" npm_config_registry="$NPM_REGISTRY"; fi; \
    corepack enable; \
    corepack prepare pnpm@10.28.0 --activate; \
    pnpm install --frozen-lockfile --ignore-scripts
COPY contracts /src/contracts
COPY ui /src/ui
# The generated client must match contracts/ (fails the build if someone forgot `pnpm generate:api`).
RUN pnpm check:api && pnpm build

FROM ${NGINX_IMAGE}
LABEL org.opencontainers.image.title="z-ui" org.opencontainers.image.source="https://github.com/K3E9X/Z"
COPY deploy/images/z-ui/nginx.conf /etc/nginx/nginx.conf
COPY deploy/images/z-ui/default.conf.template /etc/nginx/templates/default.conf.template
COPY deploy/images/z-ui/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /src/ui/dist /usr/share/nginx/html
# Rendered templates go to /tmp (the only writable path, an emptyDir/tmpfs in production): the image's
# docker-entrypoint runs envsubst at start; it needs the output directory to exist already.
ENV NGINX_ENVSUBST_OUTPUT_DIR=/tmp \
    Z_SERVER_UPSTREAM=z-server:8000 \
    Z_UI_PORT=8080 \
    NGINX_ENTRYPOINT_QUIET_LOGS=1
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s CMD wget -q -O /dev/null "http://127.0.0.1:${Z_UI_PORT}/ui-healthz" || exit 1
