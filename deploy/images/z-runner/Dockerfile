# z-runner: runs one agent job and exits (docs/04 §1, §8). Non-root, works with a read-only root filesystem:
# only /workspace and /tmp must be writable mounts (emptyDir / tmpfs). As PID 1, z-runner forwards signals and
# reaps orphaned processes itself (no extra init binary).
ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
# Runtime base: the same image by default. A pack image may start from a base that already has its build tools.
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
    uv sync --frozen --no-dev --package z-runner --no-editable --python /usr/local/bin/python3.12

FROM ${RUNTIME_IMAGE}
LABEL org.opencontainers.image.title="z-runner" org.opencontainers.image.source="https://github.com/K3E9X/Z"
# git for workspace history and the patch artifact. Build tools a pack needs (JDK, Maven, Node, ...) come from
# a derived image (docs/04 §8). Packages are fetched over HTTPS; APT_MIRROR can point at an internal mirror.
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
    && mkdir -p /workspace && chown 10001:10001 /workspace
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    TMPDIR=/tmp \
    Z_RUNNER_TMP=/tmp \
    Z_WORKSPACE=/workspace \
    OPENHANDS_SUPPRESS_BANNER=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True
WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["z-runner"]
CMD ["--help"]
