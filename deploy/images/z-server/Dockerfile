# z-server: one image, every entrypoint (api, all, migrate, ...). Non-root, read-only root filesystem friendly.
ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
ARG RUNTIME_IMAGE=${PYTHON_IMAGE}

FROM ${PYTHON_IMAGE} AS build
# Optional: internal package mirror (docs/13 §3) and a CA for TLS-intercepting proxies, passed as a BuildKit
# secret so it never lands in a layer:  docker build --secret id=extra_ca,src=/path/ca.pem ...
ARG PIP_INDEX_URL=""
ARG UV_INDEX_URL=""
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_DISABLE_PIP_VERSION_CHECK=1
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} uv==0.13.4
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/z-common packages/z-common
COPY server server
COPY runner runner
COPY cli cli
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --package z-server --no-editable --python /usr/local/bin/python3.12

FROM ${RUNTIME_IMAGE}
LABEL org.opencontainers.image.title="z-server" org.opencontainers.image.source="https://github.com/K3E9X/Z"
# git for the clone helper (git_clone workspaces) and the merge request helper. APT_MIRROR can point at an
# internal mirror; a RUNTIME_IMAGE that already has git skips the install.
ARG APT_MIRROR=https://deb.debian.org
RUN --mount=type=secret,id=extra_ca \
    if ! command -v git >/dev/null; then \
      if [ -s /run/secrets/extra_ca ]; then \
        cp /run/secrets/extra_ca /usr/local/share/ca-certificates/extra-ca.crt && update-ca-certificates >/dev/null; \
      fi; \
      sed -i "s#http://deb.debian.org#${APT_MIRROR}#g" /etc/apt/sources.list.d/debian.sources \
      && apt-get update \
      && apt-get install -y --no-install-recommends git \
      && rm -f /usr/local/share/ca-certificates/extra-ca.crt && update-ca-certificates --fresh >/dev/null \
      && rm -rf /var/lib/apt/lists/*; \
    fi \
    && groupadd --gid 10001 z && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin z \
    && mkdir -p /data/artifacts && chown -R 10001:10001 /data
COPY --from=build /app/.venv /app/.venv
COPY contracts /app/contracts
COPY packs /app/packs
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    Z_CONTRACTS_DIR=/app/contracts \
    Z_PACKS=/app/packs \
    Z_CONFIG=/etc/z/z.yaml \
    Z_ARTIFACTS_DIR=/data/artifacts
WORKDIR /app
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["z-server"]
CMD ["all"]
