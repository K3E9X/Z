"""Effective routing policy (docs/06 §3): config/z.yaml `routing_policy`, overlaid by admin edits, plus the
routing rules (match → agent / playbook) used to suggest who handles incoming work.

C2 and C3 stay in `never_external` whatever is saved (invariant 2); a policy without them is refused.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from z_common.models.enums import DataClass

from z_server.db.models import RoutingRule, Setting
from z_server.errors import Problem
from z_server.llm.routing import ALWAYS_INTERNAL

SETTING = "routing_policy"
FIELDS = (
    "data_classes",
    "default_mission_class",
    "external_fallback_when_degraded",
    "never_external",
    "never_in_memory",
)


async def effective(db: AsyncSession, config: dict[str, Any]) -> dict[str, Any]:
    base = dict(config.get("routing_policy") or {})
    row = await db.get(Setting, SETTING)
    if row is not None:
        base.update({k: v for k, v in row.value.items() if k in FIELDS})
    base["never_external"] = sorted({*base.get("never_external", []), *(c.value for c in ALWAYS_INTERNAL)})
    base.setdefault("never_in_memory", [DataClass.C3.value])
    base.setdefault("default_mission_class", DataClass.C2.value)
    base.setdefault("external_fallback_when_degraded", False)
    return base


def validate(policy: dict[str, Any]) -> None:
    missing = {c.value for c in ALWAYS_INTERNAL} - set(policy.get("never_external") or [])
    if missing:
        raise Problem("validation", f"{', '.join(sorted(missing))} can never be routed to external providers")
    if DataClass.C3.value not in set(policy.get("never_in_memory") or []):
        raise Problem("validation", "C3 content can never be kept in memory")


async def rules(db: AsyncSession, active_only: bool = True) -> list[RoutingRule]:
    stmt = select(RoutingRule).order_by(RoutingRule.id)
    if active_only:
        stmt = stmt.where(RoutingRule.active.is_(True))
    return list((await db.execute(stmt)).scalars())


def _value(event: dict[str, Any], dotted: str) -> Any:
    value: Any = event
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def matches(match: dict[str, Any], event: dict[str, Any]) -> bool:
    """Every key must match: equal (case-insensitive for strings), one of a list, or contained in a list value."""
    for key, wanted in match.items():
        have = _value(event, key)
        options = wanted if isinstance(wanted, list) else [wanted]
        norm = {str(o).lower() for o in options}
        values = have if isinstance(have, list) else [have]
        if not any(v is not None and str(v).lower() in norm for v in values):
            return False
    return True


async def route(db: AsyncSession, event: dict[str, Any]) -> tuple[str | None, str | None]:
    """The (agent, playbook) of the first active rule that matches, learned rules last."""
    ordered = sorted(await rules(db), key=lambda r: (r.origin == "learned", r.id))
    for rule in ordered:
        if rule.match and matches(rule.match, event):
            return rule.target.get("agent"), rule.target.get("playbook")
    return None, None
