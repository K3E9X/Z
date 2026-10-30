"""Routing policy (docs/06 §3, invariants 2 and 10): which model aliases a mission may use.

C2 and C3 never leave the internal cluster whatever the configuration says; external providers are used
only when enabled AND their declared data classes include the mission class. Enforcement happens in the
mission's LiteLLM virtual key (the runner never chooses).
"""

from __future__ import annotations

from typing import Any

from z_common.models.enums import DataClass

ALWAYS_INTERNAL = frozenset({DataClass.C2, DataClass.C3})


def allowed_aliases(config: dict[str, Any], data_class: DataClass) -> list[str]:
    policy = config.get("routing_policy", {})
    never_external = {DataClass(c) for c in policy.get("never_external", [])} | ALWAYS_INTERNAL
    internal: set[str] = set()
    external: set[str] = set()
    for provider in config.get("providers", []):
        if not provider.get("enabled"):
            continue
        aliases = set(provider.get("aliases", []))
        if provider.get("kind") == "internal":
            internal |= aliases
        elif data_class not in never_external and data_class.value in provider.get("data_classes", []):
            external |= aliases
    if not config.get("providers"):
        internal |= set(config.get("inference", {}).get("aliases", []))
    return sorted(internal | external)


def external_aliases(config: dict[str, Any]) -> set[str]:
    return {a for p in config.get("providers", []) if p.get("kind") == "external" for a in p.get("aliases", [])}
