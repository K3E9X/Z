"""Shared plumbing for the fakes: run an ASGI app in a background thread, and a common CLI."""

from __future__ import annotations

import argparse
import socket
import threading
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

import httpx
import uvicorn


class FakeServer:
    """Run an ASGI app with uvicorn in a daemon thread on a free port.

    Usage::

        with FakeServer(app) as srv:
            httpx.get(f"{srv.url}/health")
    """

    def __init__(self, app: Any, host: str = "127.0.0.1", port: int = 0) -> None:
        self.app = app
        self.host = host
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self.port: int = self._sock.getsockname()[1]
        config = uvicorn.Config(app, log_level="warning", lifespan="on", ws="none")
        self._server = uvicorn.Server(config)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout: float = 10.0) -> Self:
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [self._sock]}, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + timeout
        while not self._server.started:
            if not self._thread.is_alive():
                raise RuntimeError("fake server failed to start")
            if time.monotonic() > deadline:
                raise TimeoutError("fake server did not start in time")
            time.sleep(0.01)
        return self

    def stop(self, timeout: float = 10.0) -> None:
        if self._thread is None:
            return
        self._server.should_exit = True
        self._thread.join(timeout)
        self._thread = None
        self._sock.close()

    def client(self, **kwargs: Any) -> httpx.Client:
        """An httpx client bound to this server."""
        return httpx.Client(base_url=self.url, timeout=kwargs.pop("timeout", 30.0), **kwargs)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.stop()


def run_cli(
    description: str,
    default_port: int,
    build: Callable[[argparse.Namespace], Any],
    extra_args: Callable[[argparse.ArgumentParser], None] | None = None,
) -> None:
    """Common `python -m tests.fakes.<name> --host --port` entry point."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--log-level", default="info")
    if extra_args is not None:
        extra_args(parser)
    args = parser.parse_args()
    uvicorn.run(build(args), host=args.host, port=args.port, log_level=args.log_level)
