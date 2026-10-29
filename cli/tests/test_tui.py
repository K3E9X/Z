"""Textual pilots for `z top` and the interactive session, against the real server over HTTP and SSE."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from textual.pilot import Pilot
from textual.widgets import DataTable, Static
from z_cli.client import AsyncApi
from z_cli.config import Config
from z_cli.state import State
from z_cli.tui.common import Choice, ChoiceScreen, PromptScreen, TextScreen
from z_cli.tui.session import MissionBlock, PromptArea, SessionApp
from z_cli.tui.top import TopApp

from cli.tests.harness import ISSUE, LiveServer, Z, expose, now, runner_for, write_mission
from tests.support.missions import RecordingExecutor, ScriptedRunner


async def until(pilot: Pilot[Any], check: Callable[[], bool], timeout: float = 20.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await pilot.pause(0.1)


async def select(pilot: Pilot[Any], app: TopApp, predicate: Callable[[Any], bool]) -> None:
    def pick() -> bool:
        rows = app.model.rows()
        idx = next((i for i, r in enumerate(rows) if predicate(r)), None)
        if idx is None:
            return False
        app.query_one("#agents", DataTable).move_cursor(row=idx)
        row = app.selected()
        return row is not None and predicate(row)

    await until(pilot, pick, what="the row")
    await pilot.pause(0.2)


def text_of(widget: Static | Choice) -> str:
    """Plain text of what a widget shows (its Rich renderable, rendered off-screen)."""
    import io

    from rich.console import Console

    renderable = widget.content if isinstance(widget, Static) else widget.render()
    if isinstance(renderable, str):
        return renderable
    from z_cli.theme import rich_theme

    console = Console(width=200, record=True, color_system=None, file=io.StringIO(), theme=rich_theme(None))
    console.print(renderable)
    return console.export_text()


def type_in(area: PromptArea, text: str) -> None:
    """What typing does: the text, with the cursor at its end."""
    area.load_text(text)
    area.cursor_location = area.document.end


def transcript_text(app: SessionApp) -> str:
    parts = []
    for child in app.query_one("#transcript").children:
        if isinstance(child, (Static, Choice)):
            parts.append(text_of(child))
    return "\n".join(parts)


async def test_top_dashboard_decide_filter_stop(
    z: Z, live: LiveServer, executor: RecordingExecutor, tmp_path: Path
) -> None:
    alice, bob, carol = z.as_user("alice"), z.as_user("bob"), z.as_user("carol")
    live.fresh_step_up("alice")
    expose(live, alice.token(), "jira")
    # a write waiting for approval...
    write_id = write_mission(live, bob.token(), ["jira.issues.create"])
    writer = runner_for(live, executor, write_id)
    action = writer.call(
        "POST",
        "/actions",
        json={"connector": "jira", "operation": "jira.issues.create", "summary": "Create SEC ticket", "payload": ISSUE},
    ).json()
    writer.complete(output=None, status="succeeded")
    # ...and a running summarizer
    notes = tmp_path / "notes.md"
    notes.write_text("hello")
    started = bob("run", "summarize-file", "-i", "file=notes.md", "-f", str(notes), "--json")
    assert started.exit_code == 0, started.output
    import json

    summary_id = json.loads(started.output)["id"]
    runner = runner_for(live, executor, summary_id)
    runner.events(
        {"ts": now(), "type": "status", "payload": {"entity": "job", "from": "queued", "to": "running"}},
        {"ts": now(), "type": "tool_call", "payload": {"call_id": "c1", "tool": "read", "args": {"path": "notes.md"}}},
        {"ts": now(), "type": "usage", "payload": {"model": "z-fast", "tokens_in": 1500, "tokens_out": 500}},
    )

    app = TopApp(AsyncApi(live.url, carol.token()), refresh_s=1.0)
    async with app.run_test(size=(180, 50)) as pilot:
        await until(
            pilot,
            lambda: any(r.agent == "Summarizer" and r.state == "running" for r in app.model.rows()),
            what="the summarizer row",
        )
        header = text_of(app.query_one("#header", Static))
        assert "Z top" in header and "Cyber Ops" in header and "inferX" in header
        assert "not served" in header or "health" in header  # honest about /inference/health
        await until(pilot, lambda: "1 pending" in text_of(app.query_one("#review", Static)), what="review counts")
        assert "jira" in text_of(app.query_one("#connectors", Static))
        await select(pilot, app, lambda r: r.agent == "Summarizer")
        await until(pilot, lambda: "read" in text_of(app.query_one("#live", Static)), what="live events")
        row = app.selected()
        assert row is not None and row.tokens >= 2000 and row.step.endswith("/10")

        # new events arrive over SSE without waiting for a poll
        runner.events({"ts": now(), "type": "terminal", "payload": {"command": "wc -l notes.md", "exit_code": 0}})
        await until(pilot, lambda: "wc -l notes.md" in text_of(app.query_one("#live", Static)), what="SSE event")

        # filter
        await pilot.press("slash")
        assert isinstance(app.screen, PromptScreen)
        await pilot.press(*"summ", "enter")
        await until(
            pilot, lambda: all("summ" in (r.agent + r.mission_title).lower() for r in app.model.rows()), what="filter"
        )
        app.model.filter = ""

        # help
        await pilot.press("question_mark")
        assert isinstance(app.screen, TextScreen)
        await pilot.press("q")

        # approve: on an idle row, every pending decision; the action opens with its preview, 1 = Approve
        await select(pilot, app, lambda r: r.mission_id is None)
        await pilot.press("a")
        await until(pilot, lambda: isinstance(app.screen, ChoiceScreen), what="the decision")
        await pilot.press("1")
        await until(pilot, lambda: not isinstance(app.screen, ChoiceScreen), what="the decision to close")
        with live.http(carol.token()) as http:
            await until(
                pilot,
                lambda: http.get(f"/api/v1/actions/{action['id']}").json()["status"] == "approved",
                what="approval",
            )

        # stop the summarizer's mission (asks first)
        await select(pilot, app, lambda r: r.agent == "Summarizer")
        await pilot.press("x")
        await until(pilot, lambda: isinstance(app.screen, ChoiceScreen), what="the stop confirmation")
        await pilot.press("1")
        with live.http(bob.token()) as http:
            await until(
                pilot, lambda: http.get(f"/api/v1/missions/{summary_id}").json()["status"] == "cancelled", what="cancel"
            )
        await pilot.press("q")


async def test_session_prompt_completion_question_and_commands(
    z: Z, live: LiveServer, executor: RecordingExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bob = z.as_user("bob")
    monkeypatch.setenv("Z_CONFIG_DIR", str(bob.home))
    monkeypatch.setenv("Z_KEYRING", "off")
    st = State(config=Config.load(), url_override=live.url)
    app = SessionApp(st, AsyncApi(live.url, bob.token()), cwd=tmp_path)
    agent_side: dict[str, Any] = {}

    def play() -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            launched = live.dispatch(executor)
            if launched:
                r = ScriptedRunner(live.http(), launched[0], executor.launched[launched[0]]["Z_JOB_TOKEN"])  # type: ignore[arg-type]
                r.get_spec()
                r.events(
                    {"ts": now(), "type": "status", "payload": {"entity": "job", "from": "queued", "to": "running"}},
                    {
                        "ts": now(),
                        "type": "tool_call",
                        "payload": {"call_id": "c1", "tool": "read", "args": {"path": "notes.md"}},
                    },
                    {
                        "ts": now(),
                        "type": "tool_result",
                        "payload": {
                            "call_id": "c1",
                            "ok": True,
                            "summary": "5 bytes",
                            "duration_ms": 9,
                            "judge": "clean",
                        },
                    },
                )
                q = r.call("POST", "/questions", json={"text": "Which language?", "options": ["English", "French"]})
                agent_side["question"] = q.json()
                agent_side["runner"] = r
                return
            time.sleep(0.2)

    async with app.run_test(size=(150, 60)) as pilot:
        await until(pilot, lambda: bool(app.agents), what="the catalog")
        await until(pilot, lambda: "not served" in text_of(app.query_one("#welcome", Static)), what="the welcome box")
        welcome = text_of(app.query_one("#welcome", Static))
        assert "Welcome to Z" in welcome and "Cyber Ops" in welcome and "inferX" in welcome and "bob" in welcome

        area = app.query_one("#input", PromptArea)
        type_in(area, "@summ")
        await until(pilot, lambda: app.completion_open, what="completion")
        await pilot.press("tab")
        assert area.text == "@summarizer "
        area.insert("summarize the notes")
        thread = asyncio.get_running_loop().run_in_executor(None, play)
        await pilot.press("enter")
        await until(pilot, lambda: bool(app.blocks), what="a mission block")
        await thread
        block = next(iter(app.blocks.values()))
        await until(pilot, lambda: bool(app.query(Choice)), what="the inline question")
        assert "Which language?" in transcript_text(app)
        await pilot.press("2")  # French
        await until(
            pilot,
            lambda: block.view.questions and next(iter(block.view.questions.values())).answer == "French",
            what="the answer",
        )
        runner = agent_side["runner"]
        assert runner.control()["answers"][0]["answer"] == "French"

        # typing while it runs guides it
        type_in(area, "keep it short")
        await pilot.press("enter")
        await until(pilot, lambda: bool(runner.control()["messages"]), what="guidance")
        runner.complete(output={"title": "Notes", "bullets": ["one", "two"], "source": "notes.md"})
        await until(pilot, lambda: block.view.status == "succeeded", what="the mission to end")
        await until(pilot, lambda: "mission succeeded" in text_of(block) and app.focus_id is None, what="the end")

        # Shift+Tab cycles the mode
        await pilot.press("shift+tab")
        assert app.mode == "read_only"
        await pilot.press("shift+tab")
        assert app.mode == "plan_first"
        await pilot.press("shift+tab")
        assert app.mode == "propose"

        # ! runs locally, never as a mission
        type_in(area, "!echo hello-from-shell")
        await pilot.press("enter")
        await until(
            pilot,
            lambda: "hello-from-shell" in transcript_text(app) and "exit 0" in transcript_text(app),
            what="shell output",
        )
        assert len(app.blocks) == 1

        # slash commands
        type_in(area, "/help")
        await pilot.press("enter")
        await until(pilot, lambda: "/connectors" in transcript_text(app), what="help")
        type_in(area, "/connectors")
        await pilot.press("enter")
        await until(pilot, lambda: "pending_review" in transcript_text(app), what="connectors")
        type_in(area, "/usage")
        await pilot.press("enter")
        await until(pilot, lambda: "not available on this server yet" in transcript_text(app), what="usage")
        type_in(area, "/theme day")
        await pilot.press("enter")
        await until(pilot, lambda: app.theme_name == "day", what="theme")
        assert 'theme = "day"' in (bob.home / "config.toml").read_text()
        type_in(area, "/export session.md")
        await pilot.press("enter")
        await until(pilot, lambda: (tmp_path / "session.md").exists(), what="export")
        exported = (tmp_path / "session.md").read_text()
        assert "@summarizer summarize the notes" in exported and "hello-from-shell" in exported

        # history: up recalls the last entry
        await pilot.press("up")
        assert area.text == "/export session.md"
        type_in(area, "")

        # a prompt without an agent is refused with a hint, nothing is created
        type_in(area, "do something")
        await pilot.press("enter")
        await until(pilot, lambda: "Say which agent" in transcript_text(app), what="hint")
        assert len(app.blocks) == 1
        await pilot.press("ctrl+c")
    history = (bob.home / "state" / "history").read_text()
    assert "@summarizer summarize the notes" in history


async def test_session_inline_approval_and_interrupt(
    z: Z, live: LiveServer, executor: RecordingExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice, bob, carol = z.as_user("alice"), z.as_user("bob"), z.as_user("carol")
    live.fresh_step_up("alice")
    expose(live, alice.token(), "jira")
    mission_id = write_mission(live, bob.token(), ["jira.issues.create"])
    runner = runner_for(live, executor, mission_id)
    runner.events({"ts": now(), "type": "status", "payload": {"entity": "job", "from": "queued", "to": "running"}})
    monkeypatch.setenv("Z_CONFIG_DIR", str(carol.home))
    st = State(config=Config.load(), url_override=live.url)
    app = SessionApp(st, AsyncApi(live.url, carol.token()), cwd=tmp_path)
    async with app.run_test(size=(150, 60)) as pilot:
        await until(pilot, lambda: bool(app.agents), what="the catalog")
        area = app.query_one("#input", PromptArea)
        type_in(area, "/missions")
        await pilot.press("enter")
        await until(pilot, lambda: bool(app.query(Choice)), what="the mission picker")
        await pilot.press("1")
        await until(pilot, lambda: mission_id in app.blocks, what="focus")
        runner.call(
            "POST",
            "/actions",
            json={
                "connector": "jira",
                "operation": "jira.issues.create",
                "summary": "Create SEC ticket",
                "payload": ISSUE,
            },
        )
        await until(pilot, lambda: bool(app.query(Choice)), what="the inline approval")
        assert "Approve write" in transcript_text(app) and "runs as" in transcript_text(app)
        await pilot.press("1")  # Approve
        await until(pilot, lambda: "Approved jira.issues.create" in transcript_text(app), what="approved")

        # Esc asks before interrupting the focused agent
        await pilot.press("escape")
        await until(pilot, lambda: bool(app.query(Choice)), what="the interrupt prompt")
        assert "Interrupt" in transcript_text(app)
        await pilot.press("1")  # pause
        with live.http(carol.token()) as http:
            await until(
                pilot, lambda: http.get(f"/api/v1/missions/{mission_id}").json()["status"] == "paused", what="pause"
            )
        assert isinstance(app.blocks[mission_id], MissionBlock)
        await pilot.press("ctrl+c")
