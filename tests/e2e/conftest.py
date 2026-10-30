"""Fixtures of the compose e2e suite. Start the stack first (see compose.e2e.yml), then: uv run pytest -m e2e tests/e2e.
Without a stack the tests skip, except in CI (CI or Z_E2E_REQUIRED set), where they fail."""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest

from tests.e2e import stack

# runs last: it checks that every mutating call the other tests made has an audit event
AUDIT_MODULE = "test_compose_audit.py"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    items.sort(key=lambda item: item.path.name == AUDIT_MODULE)  # stable: the rest keep their order


@pytest.fixture(scope="session")
def stack_ready() -> None:
    try:
        httpx.get(f"{stack.Z}/readyz", timeout=3).raise_for_status()
    except httpx.HTTPError as exc:
        # in CI the stack was started by the job: an unreachable z-server is a failure, not a reason to skip
        if os.environ.get("CI") or os.environ.get("Z_E2E_REQUIRED"):
            pytest.fail(f"compose stack not ready at {stack.Z}/readyz: {exc}")
        pytest.skip("compose stack not running")


@pytest.fixture(scope="session")
def api(stack_ready: None) -> Iterator[httpx.Client]:
    """The dev user (auth mode dev: an org admin), over HTTP."""
    with stack.client() as c:
        yield c


@pytest.fixture
def llm(stack_ready: None) -> stack.FakeLLM:
    fake = stack.FakeLLM()
    fake.reset()
    return fake


@pytest.fixture
def tools(stack_ready: None) -> stack.FakeTools:
    fake = stack.FakeTools()
    fake.reset()
    return fake


@pytest.fixture(scope="session")
def reviewed(api: httpx.Client) -> None:
    """The admin exposure review of the pack connectors the tests use (what the packs propose; idempotent)."""
    for connector in ("jira", "logos", "defectdojo", "cmdb"):
        stack.approve_exposure(api, connector)
