"""Which mission events become learning signals (docs/08 §2)."""

from __future__ import annotations

from z_common.models.enums import SignalKind
from z_server.signals import kind_for


def test_retries_and_written_cancel_reasons_are_signals() -> None:
    retried = {"entity": "job", "from": "failed", "to": "queued", "reason": "retry of a failed job"}
    assert kind_for("status", retried) == SignalKind.retry
    automatic = {"entity": "job", "from": "running", "to": "queued", "reason": "retry 2: lease expired"}
    assert kind_for("status", automatic) is None  # the runner was lost: nothing about the agent
    cancelled = {"entity": "mission", "to": "cancelled", "reason": "wrong project, the CMDB lookup was off"}
    assert kind_for("status", cancelled) == SignalKind.feedback
    assert kind_for("status", {**cancelled, "reason": "cancelled by bob"}) is None


def test_actions_closed_by_an_ended_mission_are_not_rejections() -> None:
    by_person = {"action_id": "act_1", "from": "pending", "to": "rejected", "by": "carol", "result": {"reason": "no"}}
    assert kind_for("action_status", by_person) == SignalKind.action_rejected
    assert kind_for("action_status", {**by_person, "by": "system"}) is None
