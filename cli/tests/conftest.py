"""CLI tests against the REAL z-server app: a throwaway pgvector Postgres and the fake GitLab OIDC provider
(fixtures imported from server/tests/conftest.py), the app served by uvicorn on a free port (real HTTP, real
SSE), the scripted runner of tests/support for the agent side, and Typer's CliRunner for `z`."""

# ruff: noqa: F811 - pytest injects the imported fixtures by parameter name
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from cli.tests.harness import LiveServer, Z, serve
from server.tests.conftest import (  # noqa: F401 - pytest fixtures
    _outbound,
    database_url,
    oidc,
    oidc_server,
    pg_admin_dsn,
    template_db,
)
from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.missions import RecordingExecutor
from tests.support.server import base_config


@pytest.fixture
def live(database_url: str, oidc: FakeGitLabOIDC, tmp_path: Path) -> Iterator[LiveServer]:
    """z-server in auth mode oidc against the fake GitLab, packs seeded."""
    oidc.configure(device_interval=1)
    server = serve(database_url, base_config(oidc.issuer), tmp_path)
    server.seed()
    yield server
    server.stop()


@pytest.fixture
def live_dev(database_url: str, oidc: FakeGitLabOIDC, tmp_path: Path) -> Iterator[LiveServer]:
    """z-server in auth mode dev (a single local admin), packs seeded."""
    server = serve(database_url, base_config(oidc.issuer, **{"auth.mode": "dev"}), tmp_path)
    server.seed()
    yield server
    server.stop()


@pytest.fixture
def executor() -> RecordingExecutor:
    return RecordingExecutor()


@pytest.fixture
def z(live: LiveServer, oidc: FakeGitLabOIDC, tmp_path: Path) -> Z:
    return Z(live.url, tmp_path / "home", oidc)


@pytest.fixture
def zdev(live_dev: LiveServer, tmp_path: Path) -> Z:
    cli = Z(live_dev.url, tmp_path / "home-dev")
    result = cli("login")
    assert result.exit_code == 0, result.output
    return cli
