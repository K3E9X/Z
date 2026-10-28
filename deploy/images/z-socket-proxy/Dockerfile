# z-socket-proxy: the only path from z-server to the Docker API (deploy/images/z-socket-proxy/socket_proxy.py).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="z-socket-proxy" org.opencontainers.image.source="https://github.com/K3E9X/Z"
RUN groupadd --gid 10002 zsp && useradd --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin zsp
COPY deploy/images/z-socket-proxy/socket_proxy.py /app/socket_proxy.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10002:10002
EXPOSE 2375
HEALTHCHECK --interval=15s --timeout=3s CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:2375/_ping', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["python", "/app/socket_proxy.py"]
