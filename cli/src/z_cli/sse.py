"""Server-sent events client (contracts/README.md "SSE"): `id` = seq, `event` = type, `data` = JSON.

Resumes with `Last-Event-ID` after any disconnect, backs off exponentially with jitter (at least the
server's `retry:` value), and stops when the caller says the stream is finished (e.g. the mission ended:
z-server closes a mission stream once the mission is terminal). Errors that retrying cannot fix
(401, 403, 404, 422) are raised at once.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from z_cli.client import Api, AsyncApi
from z_cli.errors import ApiError, CliError, ExitCode, NotAvailable

FATAL_STATUS = {400, 401, 402, 403, 404, 405, 422}


@dataclass
class Message:
    event: str = "message"
    data: str = ""
    id: str | None = None
    retry_ms: int | None = None

    def json(self) -> Any:
        return json.loads(self.data) if self.data else None


@dataclass
class Parser:
    """Incremental parser of the `text/event-stream` format (WHATWG HTML §9.2)."""

    _event: str = ""
    _data: list[str] = field(default_factory=list)
    _id: str | None = None
    _retry: int | None = None

    def feed(self, line: str) -> Message | None:
        line = line.rstrip("\r\n")
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        elif name == "id" and "\0" not in value:
            self._id = value
        elif name == "retry" and value.isdigit():
            self._retry = int(value)
        return None

    def _dispatch(self) -> Message | None:
        retry, self._retry = self._retry, None
        if not self._data:
            self._event = ""
            if retry is not None:
                return Message(event="", retry_ms=retry)
            return None
        message = Message(event=self._event or "message", data="\n".join(self._data), id=self._id, retry_ms=retry)
        self._event, self._data = "", []
        return message


def parse(lines: Iterable[str]) -> Iterator[Message]:
    parser = Parser()
    for line in lines:
        message = parser.feed(line)
        if message is not None:
            yield message


@dataclass
class Backoff:
    base_s: float = 0.5
    cap_s: float = 30.0
    min_s: float = 0.0
    attempt: int = 0

    def next_delay(self) -> float:
        delay = min(self.cap_s, self.base_s * float(2 ** min(self.attempt, 16)))
        self.attempt += 1
        return max(self.min_s, delay * (0.5 + random.random() / 2))  # noqa: S311 - jitter, not crypto

    def reset(self) -> None:
        self.attempt = 0


@dataclass
class StreamState:
    last_event_id: str | None = None
    reconnects: int = 0
    connected: bool = False


def _fatal(exc: CliError) -> bool:
    """Retrying cannot fix auth, permission, missing or invalid requests."""
    if isinstance(exc, NotAvailable) or exc.exit_code in {ExitCode.AUTH, ExitCode.USAGE}:
        return True
    return isinstance(exc, ApiError) and exc.status in FATAL_STATUS


def stream(
    api: Api,
    path: str,
    *,
    last_event_id: str | None = None,
    finished: Callable[[], bool] = lambda: False,
    max_retries: int | None = None,
    state: StreamState | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[Message]:
    """Yield messages from `path` forever, reconnecting, until `finished()` is true after a disconnect."""
    state = state or StreamState(last_event_id)
    backoff = Backoff()
    failures = 0
    while True:
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        if state.last_event_id:
            headers["Last-Event-ID"] = state.last_event_id
        error: CliError | None = None
        try:
            with api.stream("GET", path, headers=headers) as response:
                if response.status_code == 204:  # nothing more will come (an ended mission)
                    return
                state.connected = True
                for message in parse(response.iter_lines()):
                    if message.retry_ms is not None:
                        backoff.min_s = message.retry_ms / 1000
                    if message.id is not None:
                        state.last_event_id = message.id
                    if message.data:
                        backoff.reset()
                        failures = 0
                        yield message
        except CliError as exc:
            if _fatal(exc):
                raise
            error = exc
        except httpx.HTTPError as exc:
            error = CliError(f"event stream interrupted: {exc}")
        finally:
            state.connected = False
        if finished():
            return
        failures += 1
        if max_retries is not None and failures > max_retries:
            raise error or CliError("event stream closed repeatedly by the server")
        state.reconnects += 1
        sleep(backoff.next_delay())


async def astream(
    api: AsyncApi,
    path: str,
    *,
    last_event_id: str | None = None,
    finished: Callable[[], Awaitable[bool]] | None = None,
    max_retries: int | None = None,
    state: StreamState | None = None,
    on_error: Callable[[CliError], None] | None = None,
) -> AsyncIterator[Message]:
    """Async twin of `stream` for the Textual apps."""
    state = state or StreamState(last_event_id)
    backoff = Backoff()
    failures = 0
    while True:
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        if state.last_event_id:
            headers["Last-Event-ID"] = state.last_event_id
        error: CliError | None = None
        try:
            async with api.stream("GET", path, headers=headers) as response:
                if response.status_code == 204:  # nothing more will come (an ended mission)
                    return
                state.connected = True
                parser = Parser()
                async for line in response.aiter_lines():
                    message = parser.feed(line)
                    if message is None:
                        continue
                    if message.retry_ms is not None:
                        backoff.min_s = message.retry_ms / 1000
                    if message.id is not None:
                        state.last_event_id = message.id
                    if message.data:
                        backoff.reset()
                        failures = 0
                        yield message
        except CliError as exc:
            if _fatal(exc):
                raise
            error = exc
        except httpx.HTTPError as exc:
            error = CliError(f"event stream interrupted: {exc}")
        finally:
            state.connected = False
        if error is not None and on_error is not None:
            on_error(error)
        if finished is not None and await finished():
            return
        failures += 1
        if max_retries is not None and failures > max_retries:
            raise error or CliError("event stream closed repeatedly by the server")
        state.reconnects += 1
        await asyncio.sleep(backoff.next_delay())
