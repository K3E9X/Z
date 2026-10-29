"""Routing policy (invariant 2, docs/06 §3): external providers are off unless enabled, they serve only the data
classes they declare, and C2/C3 never leave the internal cluster, whatever the configuration says."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from z_common.models.enums import DataClass
from z_server.llm.routing import ALWAYS_INTERNAL, allowed_aliases, external_aliases

INTERNAL = {"name": "inferX", "kind": "internal", "enabled": True, "aliases": ["z-heavy", "z-fast"]}


def external(enabled: bool = True, classes: list[str] | None = None, alias: str = "ext-model") -> dict[str, Any]:
    return {
        "name": "External",
        "kind": "external",
        "enabled": enabled,
        "aliases": [alias],
        "data_classes": ["C0", "C1", "C2", "C3"] if classes is None else classes,
    }


def config(*providers: dict[str, Any], never_external: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"providers": [INTERNAL, *providers]}
    if never_external is not None:
        out["routing_policy"] = {"never_external": never_external}
    return out


def test_the_floor_is_c2_and_c3() -> None:
    assert frozenset({DataClass.C2, DataClass.C3}) == ALWAYS_INTERNAL


@pytest.mark.parametrize("data_class", [DataClass.C2, DataClass.C3])
@pytest.mark.parametrize(
    "never_external",
    [None, [], ["C3"], ["C2"]],
    ids=["no routing_policy", "empty never_external", "only C3 listed", "only C2 listed"],
)
def test_c2_and_c3_stay_internal_even_when_an_enabled_provider_declares_them(
    data_class: DataClass, never_external: list[str] | None
) -> None:
    """An admin who enables an external provider for C2/C3 and removes them from never_external still gets the
    internal aliases only: the floor is in code, not in the configuration."""
    cfg = config(external(classes=["C0", "C1", "C2", "C3"]), never_external=never_external)
    assert allowed_aliases(cfg, data_class) == ["z-fast", "z-heavy"]
    assert not set(allowed_aliases(cfg, data_class)) & external_aliases(cfg)


@pytest.mark.parametrize("data_class", [DataClass.C2, DataClass.C3])
def test_a_provider_without_kind_counts_as_external(data_class: DataClass) -> None:
    unnamed = {"name": "Mystery", "enabled": True, "aliases": ["mystery"], "data_classes": ["C2", "C3"]}
    assert "mystery" not in allowed_aliases(config(unnamed, never_external=[]), data_class)


def test_external_providers_serve_only_enabled_and_declared_classes() -> None:
    cfg = config(external(classes=["C0"], alias="ext-c0"), external(enabled=False, classes=["C0", "C1"]))
    assert allowed_aliases(cfg, DataClass.C0) == ["ext-c0", "z-fast", "z-heavy"]
    assert allowed_aliases(cfg, DataClass.C1) == ["z-fast", "z-heavy"]  # not declared by ext-c0, other disabled


def test_never_external_can_only_add_classes() -> None:
    cfg = config(external(classes=["C0", "C1"]), never_external=["C1"])
    assert allowed_aliases(cfg, DataClass.C1) == ["z-fast", "z-heavy"]
    assert "ext-model" in allowed_aliases(cfg, DataClass.C0)


def test_the_shipped_example_keeps_external_providers_off() -> None:
    example = yaml.safe_load((Path(__file__).resolve().parents[2] / "config/z.example.yaml").read_text("utf-8"))
    assert all(not p["enabled"] for p in example["providers"] if p["kind"] != "internal")
    for data_class in DataClass:
        assert not set(allowed_aliases(example, data_class)) & external_aliases(example), data_class
