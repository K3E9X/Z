"""SSE client (parser, Last-Event-ID resume, backoff, fatal errors) and the live mission view, without a server."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from z_cli import sse
from z_cli.client import Api, AsyncApi
from z_cli.errors import ApiError, NotSignedIn
from z_cli.live import MissionView
from z_cli.render import event_line, mission_tree


def frame(seq: int, etype: str = "message", payload: dict[str, Any] | None = None) -> bytes:
    data = {
        "mission_id": "msn_1",
        "job_id": None,
        "seq": seq,
        "ts": "2026-10-28T10:00:00Z",
        "type": etype,
        "payload": payload or {},
    }
    return f"id: {seq}\nevent: {etype}\ndata: {json.dumps(data)}\n\n".encode()


class Broken(httpx.SyncByteStream):
    """Sends some frames, then the connection drops."""

    def __init__(self, chunks: list[bytes], fail: bool = True) -> None:
        self.chunks, self.fail = chunks, fail

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks
        if self.fail:
            raise httpx.ReadError("connection reset by peer")


def test_parser_handles_multiline_comments_retry_and_ids() -> None:
    lines = [": keepalive", "retry: 3000", "", "id: 7", "event: plan", 'data: {"a":', "data: 1}", "", "data: x", ""]
    messages = list(sse.parse(lines))
    assert messages[0].retry_ms == 3000 and messages[0].data == ""
    assert (messages[1].id, messages[1].event, messages[1].json()) == ("7", "plan", {"a": 1})
    assert messages[2].event == "message" and messages[2].data == "x" and messages[2].id == "7"  # id persists


def test_resume_with_last_event_id_after_a_drop() -> None:
    seen_headers: list[str | None] = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("last-event-id"))
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=Broken([b"retry: 10\n\n", frame(1), frame(2)]),
            )
        if calls["n"] == 2:
            return httpx.Response(
                503, json={"type": "urn:z:problem:unavailable", "title": "Service unavailable", "status": 503}
            )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Broken([frame(3)], fail=False))

    api = Api("http://z.test", "zs_t", transport=httpx.MockTransport(handler))
    sleeps: list[float] = []
    state = sse.StreamState()
    got = [
        m.json()["seq"]
        for m in sse.stream(
            api, "/missions/msn_1/events", finished=lambda: calls["n"] >= 3, state=state, sleep=sleeps.append
        )
    ]
    assert got == [1, 2, 3]
    assert seen_headers == [None, "2", "2"]  # resumed after the last delivered event, also after a 503
    assert state.reconnects == 2 and all(s >= 0.01 for s in sleeps)  # never below the server's retry hint


def test_backoff_grows_and_caps() -> None:
    b = sse.Backoff(base_s=1, cap_s=8)
    delays = [b.next_delay() for _ in range(6)]
    assert delays[0] <= 1 and delays[3] >= 4 and max(delays) <= 8
    b.reset()
    assert b.next_delay() <= 1


@pytest.mark.parametrize("status", [401, 403, 404])
def test_fatal_errors_are_not_retried(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "type": "urn:z:problem:forbidden" if status == 403 else "urn:z:problem:unauthenticated",
            "title": "x",
            "status": status,
        }
        if status == 404:
            body = {"type": "urn:z:problem:not-found", "title": "Not found", "status": 404}
        return httpx.Response(status, json=body, headers={"content-type": "application/problem+json"})

    api = Api("http://z.test", "zs_t", transport=httpx.MockTransport(handler))
    with pytest.raises(ApiError):
        list(sse.stream(api, "/missions/msn_1/events", sleep=lambda s: None))
    anon = Api("http://z.test", None, transport=httpx.MockTransport(handler))
    if status == 401:
        with pytest.raises(NotSignedIn):
            list(sse.stream(anon, "/missions/msn_1/events", sleep=lambda s: None))


def test_max_retries_gives_up() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    api = Api("http://z.test", "zs_t", transport=httpx.MockTransport(handler))
    with pytest.raises(Exception, match="Cannot reach Z"):
        list(sse.stream(api, "/x", max_retries=2, sleep=lambda s: None))


async def test_async_stream_resumes() -> None:
    calls = {"n": 0}
    headers: list[str | None] = []

    class ABroken(httpx.AsyncByteStream):
        def __init__(self, chunks: list[bytes], fail: bool) -> None:
            self.chunks, self.fail = chunks, fail

        async def __aiter__(self) -> AsyncIterator[bytes]:
            for chunk in self.chunks:
                yield chunk
            if self.fail:
                raise httpx.ReadError("reset")

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        headers.append(request.headers.get("last-event-id"))
        if calls["n"] == 1:
            return httpx.Response(200, stream=ABroken([frame(1)], True))
        return httpx.Response(200, stream=ABroken([frame(2)], False))

    api = AsyncApi("http://z.test", "zs_t", transport=httpx.MockTransport(handler))

    async def finished() -> bool:
        return calls["n"] >= 2

    got = [m.json()["seq"] async for m in sse.astream(api, "/e", finished=finished)]
    await api.aclose()
    assert got == [1, 2] and headers == [None, "1"]


def ev(
    seq: int,
    etype: str,
    payload: dict[str, Any],
    job: str | None = "job_1",
    agent: str | None = "vuln-analyst",
    step: str | None = "qualify",
) -> dict[str, Any]:
    return {
        "mission_id": "msn_1",
        "job_id": job,
        "seq": seq,
        "ts": f"2026-10-28T10:00:{seq:02d}Z",
        "type": etype,
        "agent": agent,
        "step_id": step,
        "payload": payload,
    }


def test_mission_view_folds_events() -> None:
    view = MissionView("msn_1")
    events = [
        ev(1, "status", {"entity": "mission", "from": None, "to": "queued"}, job=None, agent=None, step=None),
        ev(
            2,
            "plan",
            {
                "steps": [
                    {"id": "pull", "agent": "vuln-analyst", "title": "Pull findings"},
                    {
                        "id": "qualify",
                        "agent": "vuln-analyst",
                        "title": "Qualify each finding",
                        "writes": ["jira.issues.create"],
                    },
                ],
                "writes": ["jira.issues.create"],
            },
            job=None,
            agent=None,
            step=None,
        ),
        ev(3, "status", {"entity": "job", "id": "job_1", "from": "queued", "to": "running"}),
        ev(
            4,
            "tool_call",
            {
                "call_id": "c1",
                "tool": "defectdojo__findings__get",
                "operation": "defectdojo.findings.get",
                "args": {"id": 18422},
            },
        ),
        ev(
            5,
            "tool_result",
            {"call_id": "c1", "ok": True, "summary": "1 finding", "duration_ms": 300, "judge": "clean"},
        ),
        ev(
            6, "denied", {"what": "curl https://nvd.nist.gov", "rule": "egress", "detail": "network egress not allowed"}
        ),
        ev(7, "usage", {"model": "z-fast", "tokens_in": 1000, "tokens_out": 200}),
        ev(
            8,
            "action_proposed",
            {
                "action_id": "act_1",
                "connector": "jira",
                "operation": "jira.issues.create",
                "class": "write",
                "summary": "3 tickets",
            },
        ),
        ev(9, "question", {"question_id": "q_1", "text": "Mark it as failed?", "options": ["yes", "no"]}),
        ev(
            10,
            "status",
            {"entity": "mission", "from": "running", "to": "waiting", "reason": "approval"},
            job=None,
            agent=None,
            step=None,
        ),
    ]
    for e in events:
        view.apply(e)
    view.apply(events[3])  # a replayed event is ignored
    assert view.status == "waiting" and view.waiting_reason == "approval"
    assert list(view.steps) == ["pull", "qualify"] and view.writes == ["jira.issues.create"]
    job = view.jobs["job_1"]
    assert job.status == "running" and len(job.calls) == 1 and job.tokens == 1200 and job.model == "z-fast"
    assert view.step_status(view.steps["qualify"]) == "running" and view.step_status(view.steps["pull"]) == "pending"
    assert [a.id for a in view.pending_actions()] == ["act_1"] and [q.id for q in view.open_questions()] == ["q_1"]
    view.apply(ev(11, "action_status", {"action_id": "act_1", "from": "pending", "to": "approved", "by": "carol"}))
    view.apply(ev(12, "question_answered", {"question_id": "q_1", "answer": "yes", "answered_by": "bob"}))
    assert not view.pending_actions() and not view.open_questions()

    from rich.console import Console

    console = Console(width=120, record=True, color_system=None)
    console.print(mission_tree(view))
    text = console.export_text()
    assert "Qualify each finding" in text and "defectdojo.findings.get(id=18422)" in text
    assert "blocked curl https://nvd.nist.gov" in text and "network egress not allowed" in text
    lines = [event_line(e).plain for e in events]
    assert "approval" in lines[7] and "jira.issues.create" in lines[7]
    assert "blocked" in lines[5]
