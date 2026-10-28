from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.fakes.fake_tools import FakeTools


@pytest.fixture(scope="module")
def tools() -> Iterator[FakeTools]:
    with FakeTools() as server:
        yield server
