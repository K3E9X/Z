"""Control channel (contracts/README.md "Runner protocol" step 4): the `JobControl` returned by event and
checkpoint posts, and `GET /internal/jobs/{id}/control?wait_s=` while blocked.

`ControlHub` keeps what the runner still has to act on: pause/cancel, "Guide the agent" messages not yet
injected, answers to questions, action status changes and a rotated LLM key. Ids already handled are
remembered (and checkpointed) so a message or answer is never delivered twice.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Literal

from z_common.models.api_internal import Answer, GuideMessage, JobControl

State = Literal["run", "pause", "cancel"]


class ControlHub:
    def __init__(self, fetch: Callable[[int], JobControl], flush: Callable[[], JobControl | None]) -> None:
        self._fetch = fetch
        self._flush = flush
        self._lock = threading.Lock()
        self.state: State = "run"
        self.reason: str | None = None
        self._messages: list[GuideMessage] = []
        self._answers: dict[str, Answer] = {}
        self._action_status: dict[str, str] = {}
        self._action_updates: list[tuple[str, str]] = []
        self._llm_key: str | None = None
        self.seen_messages: set[str] = set()
        self.seen_answers: set[str] = set()

    # ------------------------------------------------------------------ input
    def ingest(self, control: JobControl | None) -> None:
        if control is None:
            return
        with self._lock:
            self.state = control.state
            self.reason = control.reason
            for message in control.messages:
                if message.id not in self.seen_messages and all(m.id != message.id for m in self._messages):
                    self._messages.append(message)
            for answer in control.answers:
                if answer.question_id not in self.seen_answers:
                    self._answers[answer.question_id] = answer
            for action in control.actions:
                status = action.status.value
                if self._action_status.get(action.action_id) != status:
                    self._action_status[action.action_id] = status
                    self._action_updates.append((action.action_id, status))
            if control.llm_api_key:
                self._llm_key = control.llm_api_key

    def poll(self, wait_s: int = 0) -> None:
        """Flush pending events (their response carries control too), then read the control endpoint."""
        self.ingest(self._flush())
        self.ingest(self._fetch(wait_s))

    # ------------------------------------------------------------------ output
    @property
    def cancelled(self) -> bool:
        return self.state == "cancel"

    @property
    def paused(self) -> bool:
        return self.state == "pause"

    def take_messages(self) -> list[GuideMessage]:
        with self._lock:
            messages, self._messages = self._messages, []
            self.seen_messages.update(m.id for m in messages)
            return messages

    def take_answer(self, question_id: str) -> Answer | None:
        with self._lock:
            answer = self._answers.pop(question_id, None)
            if answer is not None:
                self.seen_answers.add(question_id)
            return answer

    def take_unclaimed_answers(self) -> list[Answer]:
        """Answers to questions no tool is waiting for (e.g. asked before a pause or a restart)."""
        with self._lock:
            answers = list(self._answers.values())
            self._answers.clear()
            self.seen_answers.update(a.question_id for a in answers)
            return answers

    def take_action_updates(self) -> list[tuple[str, str]]:
        with self._lock:
            updates, self._action_updates = self._action_updates, []
            return updates

    def take_llm_key(self) -> str | None:
        with self._lock:
            key, self._llm_key = self._llm_key, None
            return key

    # ------------------------------------------------------------------ checkpoint
    def to_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "seen_messages": sorted(self.seen_messages),
                "seen_answers": sorted(self.seen_answers),
                "action_status": dict(self._action_status),
            }

    def load_state(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.seen_messages = set(data.get("seen_messages", []))
            self.seen_answers = set(data.get("seen_answers", []))
            self._action_status = dict(data.get("action_status", {}))
