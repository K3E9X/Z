# z-browser: the gateway-side browser worker (Playwright + headless Chromium). Non-root, read-only root
# filesystem friendly: Chromium writes only to /tmp (mount an emptyDir or tmpfs there).
# Base: Microsoft's Playwright image for the pinned Playwright (Chromium build and its system libraries), so the
# build needs no apt access. Keep the tag in step with `playwright==` in browser/pyproject.toml.
ARG PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright/python:v1.63.0-noble@sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0

FROM ${PLAYWRIGHT_IMAGE} AS build
ARG PIP_INDEX_URL=""
ARG UV_INDEX_URL=""
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_DISABLE_PIP_VERSION_CHECK=1
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir --break-system-packages ${PIP_INDEX_URL:+--index-url "$PIP_INDEX_URL"} uv==0.13.4
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/z-common packages/z-common
COPY server/pyproject.toml server/pyproject.toml
COPY runner/pyproject.toml runner/pyproject.toml
COPY cli/pyproject.toml cli/pyproject.toml
COPY browser browser
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --package z-browser --no-editable --python /usr/bin/python3.12

FROM ${PLAYWRIGHT_IMAGE}
LABEL org.opencontainers.image.title="z-browser" org.opencontainers.image.source="https://github.com/K3E9X/Z"
# Only Chromium is used: the other engines of the base image are removed from what the worker can launch, and
# so are pip and its caches (the worker never installs anything; pip vendors its own certifi bundle).
RUN rm -rf /ms-playwright/firefox-* /ms-playwright/webkit-* /root/.cache \
       /usr/local/lib/python3.12/dist-packages/pip /usr/local/lib/python3.12/dist-packages/pip-*.dist-info \
       /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
    && groupadd --gid 10001 z && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin z
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
WORKDIR /app
USER 10001:10001
EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["z-browser"]
