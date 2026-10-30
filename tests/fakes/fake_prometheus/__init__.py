"""Fake Prometheus (vLLM, DCGM, kube-state-metrics series). See tests/fakes/README.md."""

from __future__ import annotations

from typing import Any

from .._server import FakeServer
from .app import SERIES, classify, create_app

__all__ = ["SERIES", "FakePrometheus", "classify", "create_app"]


class FakePrometheus(FakeServer):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(create_app(), **kwargs)

    def set_values(self, values: dict[str, Any]) -> None:
        with self.client() as c:
            c.post("/_fake/values", json=values).raise_for_status()

    def clear_values(self) -> None:
        with self.client() as c:
            c.delete("/_fake/values").raise_for_status()

    def down(self, down: bool = True) -> None:
        with self.client() as c:
            c.post("/_fake/down", json={"down": down}).raise_for_status()

    def reset(self) -> None:
        with self.client() as c:
            c.post("/_fake/reset").raise_for_status()
