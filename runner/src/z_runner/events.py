"""Event sink: every Z event the runner produces goes through here (docs/03 §3, contracts/events.schema.json).

Payloads are redacted, size-capped and validated against the z-common payload models before they are buffered.
`flush()` posts the buffer as one `EventBatch` and returns the server's `JobControl`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError
from z_common.models.api_internal import EventBatch, JobControl
from z_common.models.events import EventIn, EventType, parse_payload

from z_runner.redaction import Redactor

log = logging.getLogger("z_runner.events")

MAX_STRING = 32_000
TRUNCATED = "…[truncated]"


class InvalidEvent(ValueError):
    """An event payload does not match its contract model (a runner bug)."""


class EventSink(Protocol):
    """What backends and tools use to report events."""

    def emit(self, type: EventType, payload: Mapping[str, Any]) -> None: ...


def cap(value: Any, limit: int = MAX_STRING) -> Any:
    """Cap every string in a JSON-like value."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[: limit - len(TRUNCATED)] + TRUNCATED
    if isinstance(value, dict):
        return {k: cap(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [cap(v, limit) for v in value]
    return value


class EventBuffer:
    """Thread-safe buffer of `EventIn` with redaction and contract validation."""

    def __init__(
        self,
        post: Callable[[EventBatch], JobControl],
        redactor: Redactor,
        step_id: str | None,
        *,
        strict: bool = False,
        max_batch: int = 200,
    ) -> None:
        self._post = post
        self._redactor = redactor
        self._step_id = step_id
        self._strict = strict
        self._max_batch = max_batch
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._events: list[EventIn] = []
        self.dropped = 0
        self.sent = 0

    def emit(self, type: EventType, payload: Mapping[str, Any]) -> None:
        clean = cap(self._redactor.value(dict(payload)))
        try:
            parse_payload(type, clean)
            event = EventIn(ts=datetime.now(UTC), type=type, step_id=self._step_id, payload=clean)
        except (ValidationError, KeyError) as exc:
            self.dropped += 1
            log.error("dropping invalid %s event: %s", type, exc)
            if self._strict:
                raise InvalidEvent(f"{type}: {exc}") from exc
            return
        with self._lock:
            self._events.append(event)

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._events)

    def flush(self) -> JobControl | None:
        """Post buffered events (in batches). Returns the last JobControl, or None when nothing was sent.

        On failure the unsent events stay buffered (in order) and the error is raised.
        """
        control: JobControl | None = None
        with self._flush_lock:
            while True:
                with self._lock:
                    batch = self._events[: self._max_batch]
                if not batch:
                    return control
                control = self._post(EventBatch(events=batch))
                with self._lock:
                    del self._events[: len(batch)]
                self.sent += len(batch)
