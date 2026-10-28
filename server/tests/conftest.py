"""Fixtures: a real Postgres (Z_TEST_DATABASE_URL or a throwaway pgvector container), the fake GitLab OIDC
provider, and a TestClient on https://testserver (so __Host- cookies work)."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient
from z_server.app import create_app
from z_server.settings import Settings

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import REPO, SECRET, base_config, contract_checker

TEMPLATE = "z_template"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_pg(dsn: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=2):
                return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="session")
def pg_admin_dsn() -> Iterator[str]:
    """DSN of a database where we may CREATE DATABASE."""
    if os.environ.get("Z_TEST_DATABASE_URL"):
        yield os.environ["Z_TEST_DATABASE_URL"]
        return
    if not shutil.which("docker"):
        pytest.fail("Postgres is required: set Z_TEST_DATABASE_URL or install Docker")
    port, name = _free_port(), f"z-test-pg-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            "POSTGRES_USER=z",
            "-e",
            "POSTGRES_PASSWORD=z",
            "-e",
            "POSTGRES_DB=postgres",
            "-p",
            f"127.0.0.1:{port}:5432",
            "pgvector/pgvector:pg16",
        ],
        check=True,
        capture_output=True,
    )
    dsn = f"postgresql://z:z@127.0.0.1:{port}/postgres"
    try:
        _wait_pg(dsn)
        yield dsn
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def _with_db(dsn: str, database: str) -> str:
    parsed = urlparse(dsn)
    return parsed._replace(path=f"/{database}").geturl()


@pytest.fixture(scope="session")
def template_db(pg_admin_dsn: str) -> str:
    with psycopg.connect(pg_admin_dsn, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEMPLATE}")
        conn.execute(f"CREATE DATABASE {TEMPLATE}")
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'z_app'").fetchone():
            conn.execute("CREATE ROLE z_app LOGIN PASSWORD 'z_app'")
    env = {**os.environ, "DATABASE_URL": _with_db(pg_admin_dsn, TEMPLATE)}
    subprocess.run(
        ["alembic", "-c", str(REPO / "server/src/z_server/db/alembic.ini"), "upgrade", "head"],
        check=True,
        env=env,
        capture_output=True,
    )
    return TEMPLATE


@pytest.fixture
def database_url(pg_admin_dsn: str, template_db: str) -> Iterator[str]:
    name = f"z_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(pg_admin_dsn, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name} TEMPLATE {template_db}")
    yield _with_db(pg_admin_dsn, name).replace("postgresql://", "postgresql+psycopg://")
    with psycopg.connect(pg_admin_dsn, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture(scope="session")
def oidc_server() -> Iterator[FakeGitLabOIDC]:
    with FakeGitLabOIDC() as server:
        yield server


@pytest.fixture
def oidc(oidc_server: FakeGitLabOIDC) -> FakeGitLabOIDC:
    oidc_server.reset()
    oidc_server.add_user("victor", ["cyber/other-team"])  # in an allowed group but no mapped group -> viewer
    return oidc_server


@pytest.fixture(autouse=True)
def _outbound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Z_OUTBOUND_ALLOW", "127.0.0.1,localhost")


@pytest.fixture
def make_client(database_url: str, oidc: FakeGitLabOIDC) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def factory(config: dict[str, Any] | None = None, **settings_kwargs: Any) -> TestClient:
        settings = Settings(
            database_url=database_url,
            secret_key=SECRET,
            public_url="https://testserver",
            config=config or base_config(oidc.issuer),
            **{"oidc_client_secret": "z-web-secret", **settings_kwargs},
        )
        app = create_app(settings)
        client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
        client.event_hooks["response"].append(contract_checker(app))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()
