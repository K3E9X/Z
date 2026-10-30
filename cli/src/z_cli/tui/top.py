"""`z top`: full-screen live dashboard of every agent (docs/10 §2, design/mockups/CLI.html).

Data: the REST lists (missions, jobs, agents, connectors, review) refreshed every few seconds, and the
event stream of every active mission over SSE (resumed with Last-Event-ID after a drop). When the server
serves the global `GET /stream`, its status messages trigger refreshes between polls.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from rich.console import Group, RenderableType
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import DataTable, Static
from textual.worker import Worker, WorkerState

from z_cli import contract, sse
from z_cli.client import AsyncApi
from z_cli.errors import CliError, NotAvailable
from z_cli.live import TERMINAL_JOB, MissionView
from z_cli.render import age, clock, duration, mission_tree, ms, since, tokens
from z_cli.state import State
from z_cli.theme import state_style
from z_cli.tui import decisions
from z_cli.tui.common import ChoiceScreen, PromptScreen, TextScreen, apply_theme

ACTIVE = "queued,running,waiting,paused"
HEALTH = {
    "healthy": ("●", "z.ok"),
    "degraded": ("▲", "z.wait"),
    "token_expiring": ("▲", "z.wait"),
    "unreachable": ("✕", "z.err"),
    "disabled": ("○", "z.d"),
}
HELP = """\
z top: every agent, live.

  ↑ ↓      select a row
  ↵        open the mission (plan, jobs, tool calls)
  a        approve: open the pending decisions of the selected mission (all of them without a mission)
  l        logs of the selected agent
  p        pause or resume the selected mission
  t        take over: print the link to the job's take-over page in the web app (read-only by default)
  x        stop (cancel) the selected mission
  r        refresh now
  /        filter rows (agent, mission, state)
  :        command: review, refresh, filter <text>, theme <night|day|slate|contrast>, quit
  ?        this help
  q        quit
"""


@dataclass
class Row:
    key: str
    agent: str
    agent_name: str
    mission_id: str | None
    mission_title: str
    state: str
    step: str
    tokens: int
    started: datetime | None
    job_id: str | None

    def matches(self, text: str) -> bool:
        needle = text.lower()
        return any(
            needle in (v or "").lower()
            for v in (self.agent, self.agent_name, self.mission_title, self.state, self.mission_id)
        )


@dataclass
class TopModel:
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    missions: dict[str, dict[str, Any]] = field(default_factory=dict)
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    views: dict[str, MissionView] = field(default_factory=dict)
    connectors: list[dict[str, Any]] = field(default_factory=list)
    review: dict[str, Any] | None = None
    oldest_review_s: int | None = None
    me: dict[str, Any] | None = None
    inference: dict[str, Any] | None = None
    inference_note: str = ""
    filter: str = ""
    stream_note: str = ""
    error: str = ""
    refreshed_at: datetime | None = None

    def view(self, mission_id: str) -> MissionView:
        if mission_id not in self.views:
            self.views[mission_id] = MissionView(mission_id)
            self.views[mission_id].use_agents(list(self.agents.values()))
            if mission_id in self.missions:
                self.views[mission_id].load_mission(self.missions[mission_id])
        return self.views[mission_id]

    def job_state(self, job: dict[str, Any]) -> str:
        status = str(job.get("status"))
        if status == "needs_attention":
            return str(job.get("needs_attention_reason") or "attention")
        return status

    def rows(self) -> list[Row]:
        rows: list[Row] = []
        busy: set[str] = set()
        for job in self.jobs.values():
            if job.get("status") in TERMINAL_JOB:
                continue
            mission = self.missions.get(job["mission_id"])
            if mission is None:
                continue
            name = str(job.get("agent"))
            agent = self.agents.get(name, {})
            busy.add(name)
            view = self.views.get(job["mission_id"])
            live_job = view.jobs.get(job["id"]) if view else None
            max_steps = (agent.get("limits") or {}).get("max_steps")
            used = int(job.get("tokens_in", 0)) + int(job.get("tokens_out", 0))
            if live_job is not None:
                used = max(used, live_job.tokens)
            state = self.job_state(job)
            if live_job is not None and live_job.status not in {"queued"}:
                state = live_job.reason if live_job.status == "needs_attention" and live_job.reason else live_job.status
            rows.append(
                Row(
                    key=job["id"],
                    agent=str(agent.get("display_name") or name),
                    agent_name=name,
                    mission_id=job["mission_id"],
                    mission_title=str(mission.get("title", "")),
                    state=state,
                    step=f"{job.get('steps_done', 0)}/{max_steps}" if max_steps else str(job.get("steps_done", 0)),
                    tokens=used,
                    started=_ts(job.get("started_at")) or (live_job.started if live_job else None),
                    job_id=job["id"],
                )
            )
        rows.sort(key=lambda r: (r.state not in {"approval", "question", "attention"}, r.state != "running", r.agent))
        for name, agent in sorted(self.agents.items(), key=lambda kv: str(kv[1].get("display_name"))):
            if name in busy or agent.get("status") == "disabled":
                continue
            rows.append(
                Row(f"agent:{name}", str(agent.get("display_name") or name), name, None, "—", "idle", "", 0, None, None)
            )
        if self.filter:
            rows = [r for r in rows if r.matches(self.filter)]
        return rows

    def header(self) -> Text:
        t = Text(" Z top", style="z.b")
        ws = (self.me or {}).get("workspace") or {}
        team = ", ".join(x["name"] for x in ((self.me or {}).get("user") or {}).get("teams") or [])
        bits = [b for b in (team, ws.get("name") if not team else None, ws.get("environment")) if b]
        t.append("  " + " · ".join(bits), style="z.d")
        t.append("    inferX ", style="z.d")
        inf = self.inference
        if inf:
            glyph, style = HEALTH.get("healthy" if inf.get("status") == "healthy" else "degraded", ("?", "z.d"))
            t.append(glyph, style=style)
            models = inf.get("models") or []
            tps = sum(m.get("tokens_per_s") or 0 for m in models)
            waiting = sum(m.get("requests_waiting") or 0 for m in models)
            p95 = max((m.get("ttft_p95_ms") or 0 for m in models), default=0)
            t.append(
                f" {tps:,.0f} tok/s · queue {waiting:.0f} · p95 {ms(int(p95))} · {tokens(inf.get('tokens_today'))} tokens today",
                style="z.d",
            )
        else:
            t.append("?", style="z.d")
            t.append(f" {self.inference_note or 'health not loaded yet'}", style="z.d")
        queued = sum(1 for j in self.jobs.values() if j.get("status") == "queued")
        t.append(f" · jobs queued {queued}", style="z.d")
        if self.stream_note:
            t.append(f" · {self.stream_note}", style="z.d")
        if self.error:
            t.append(f"  {self.error}", style="z.err")
        return t

    def connectors_line(self) -> Text:
        t = Text(" CONNECTORS  ", style="z.d")
        for c in self.connectors:
            health = (c.get("health") or {}).get("status", "?")
            glyph, style = HEALTH.get(health, ("?", "z.d"))
            t.append(f"{c['name']} ")
            label = glyph if health == "healthy" else f"{glyph} {health.replace('_', ' ')}"
            t.append(label + "  ", style=style)
        if not self.connectors:
            t.append("none", style="z.d")
        return t

    def review_line(self) -> Text:
        t = Text(" REVIEW      ", style="z.d")
        counts = (self.review or {}).get("counts") or {}
        n = int(counts.get("all", 0))
        t.append(f"{n} pending", style="z.wait" if n else "z.d")
        if n:
            t.append(f"  ·  {counts.get('writes', 0)} writes, {counts.get('questions', 0)} questions", style="z.d")
            if self.oldest_review_s is not None:
                t.append(f"  ·  oldest {age(self.oldest_review_s)}", style="z.d")
        return t

    def live_lines(self, row: Row | None, limit: int = 12) -> list[Text]:
        if row is None or row.mission_id is None or row.mission_id not in self.views:
            return [Text(" no live events for this row", style="z.d")]
        view = self.views[row.mission_id]
        entries = [e for e in view.log if e.job_id in (row.job_id, None) or e.agent == row.agent_name]
        out = []
        for entry in list(entries)[-limit:]:
            line = Text(f" {clock(entry.ts)} ", style="z.d")
            line.append(f"{entry.kind:<10} ", style=entry.style or "z.text")
            line.append(entry.text[:160], style=entry.style or "z.m")
            out.append(line)
        return out or [Text(" waiting for events…", style="z.d")]


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


class ModalPresenter:
    def __init__(self, app: TopApp) -> None:
        self.app = app

    async def choose(self, body: RenderableType | None, options: list[str], *, danger: bool = False) -> int | None:
        return await self.app.push_screen_wait(ChoiceScreen(body, options, danger=danger))

    async def ask_text(self, title: str, placeholder: str = "") -> str | None:
        return await self.app.push_screen_wait(PromptScreen(title, placeholder))

    async def edit_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return edit_with_suspend(self.app, payload)

    def info(self, text: RenderableType) -> None:
        self.app.notify(text.plain if isinstance(text, Text) else str(text), timeout=6)

    def error(self, exc: CliError) -> None:
        self.app.notify(exc.message + (f"\n{exc.hint}" if exc.hint else ""), severity="error", timeout=10)


def edit_with_suspend(app: App[Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    from z_cli import state as state_mod

    editor = state_mod.get().setting("editor") or os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        path = Path(fh.name)
    try:
        with app.suspend():
            # The local user's own $VISUAL/$EDITOR on their own temp file, argv without a shell.
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args
            subprocess.run([*shlex.split(editor), str(path)], check=False)  # noqa: S603
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        app.notify(f"payload not changed: {exc}", severity="error")
        return None
    finally:
        path.unlink(missing_ok=True)
    return data if isinstance(data, dict) else None


class TopApp(App[int]):
    TITLE = "z top"
    CSS = """
    Screen { layout: vertical; }
    #header { height: 1; margin: 1 1 1 1; }
    #agents { height: 1fr; min-height: 5; margin: 0 1; }
    #connectors, #review { height: 1; margin: 0 1; }
    #live-title { height: 1; margin: 1 1 0 1; }
    #live { height: 13; margin: 0 1; }
    #keys { height: 1; margin: 1 1 0 1; }
    DataTable > .datatable--header { color: $secondary; text-style: none; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "open", "open", show=False),
        Binding("a", "approve", "approve", show=False),
        Binding("l", "logs", "logs", show=False),
        Binding("p", "pause", "pause", show=False),
        Binding("t", "take_over", "take over", show=False),
        Binding("x", "stop", "stop", show=False),
        Binding("r", "refresh", "refresh", show=False),
        Binding("slash", "filter", "filter", show=False),
        Binding("colon", "command", "command", show=False),
        Binding("question_mark", "help", "help", show=False),
        Binding("q", "quit", "quit", show=False),
    ]

    def __init__(self, api: AsyncApi, *, refresh_s: float = 5.0, theme_name: str | None = None) -> None:
        super().__init__()
        self.api = api
        self.refresh_s = refresh_s
        self.theme_name = theme_name
        self.model = TopModel()
        self.streams: dict[str, asyncio.Task[None]] = {}
        self.stream_states: dict[str, sse.StreamState] = {}
        self._rows: list[Row] = []
        self._inference_available = True
        self._wake = asyncio.Event()
        self._ui_ready = False

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        table: DataTable[Any] = DataTable(id="agents", cursor_type="row", zebra_stripes=False)
        yield table
        yield Static(id="connectors")
        yield Static(id="review")
        yield Static(id="live-title")
        yield Static(id="live")
        yield Static(self._keys(), id="keys")

    def _keys(self) -> Text:
        t = Text(" ")
        for key, label in (
            ("↵", "open"),
            ("a", "approve"),
            ("l", "logs"),
            ("p", "pause"),
            ("t", "take over"),
            ("x", "stop"),
            ("/", "filter"),
            (":", "command"),
            ("?", "help"),
        ):
            t.append(f" {key} ", style="z.key")
            t.append(f" {label}  ", style="z.d")
        return t

    def on_mount(self) -> None:
        apply_theme(self, self.theme_name)
        table = self.query_one("#agents", DataTable)
        for label, width in (("AGENT", 22), ("MISSION", 46), ("STATE", 11), ("STEP", 7), ("TOKENS", 8), ("TIME", 7)):
            table.add_column(label, width=width, key=label.lower())
        self._ui_ready = True
        self.render_all()
        self.poll()
        self.global_stream()

    # ---------------------------------------------------------------- data

    @work(exclusive=True, group="poll", exit_on_error=False)
    async def poll(self) -> None:
        while True:
            await self.refresh_data()
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.refresh_s)

    async def refresh_data(self) -> None:
        m = self.model
        try:
            if m.me is None:
                m.me = await self.api.get("/me")
            m.agents = {a["name"]: a for a in await self.api.items("/agents", limit=200)}
            m.connectors = list(await self.api.get("/connectors"))
            m.review = await self.api.get("/review", {"limit": 200})
            ages = [int(i["age_s"]) for i in (m.review or {}).get("items", [])]
            m.oldest_review_s = max(ages) if ages else None
            missions = await self.api.items("/missions", {"status": ACTIVE}, limit=200)
            active = {x["id"]: x for x in missions}
            for mid in list(m.missions):
                if mid not in active:
                    m.missions.pop(mid)
                    m.views.pop(mid, None)
            jobs: dict[str, dict[str, Any]] = {}
            for mission in missions:
                m.missions[mission["id"]] = mission
                m.view(mission["id"]).load_mission(mission)
                for job in await self.api.items(
                    f"/missions/{mission['id']}/jobs", {"status": "queued,running,needs_attention,paused"}
                ):
                    jobs[job["id"]] = job
                self.ensure_stream(mission["id"])
            m.jobs = jobs
            if self._inference_available:
                try:
                    m.inference = await self.api.get("/inference/health")
                except NotAvailable:
                    self._inference_available = False
                    m.inference, m.inference_note = None, "health not served by this Z server yet"
                except CliError as exc:
                    m.inference, m.inference_note = None, exc.message
            m.error = ""
        except CliError as exc:
            m.error = exc.message
        m.refreshed_at = datetime.now(UTC)
        self.render_all()

    def ensure_stream(self, mission_id: str) -> None:
        task = self.streams.get(mission_id)
        if task is None or task.done():
            self.streams[mission_id] = asyncio.create_task(self.follow(mission_id), name=f"sse-{mission_id}")

    async def follow(self, mission_id: str) -> None:
        state = self.stream_states.setdefault(mission_id, sse.StreamState())

        async def finished() -> bool:
            mission = self.model.missions.get(mission_id)
            view = self.model.views.get(mission_id)
            return mission is None or (view is not None and view.terminal)

        def on_error(exc: CliError) -> None:
            self.model.stream_note = f"reconnecting ({exc.message[:40]})"

        try:
            async for message in sse.astream(
                self.api, f"/missions/{mission_id}/events", finished=finished, state=state, on_error=on_error
            ):
                event = contract.check_event(message.json())
                view = self.model.view(mission_id)
                view.apply(event)
                if event.get("type") == "status":
                    payload = event.get("payload") or {}
                    if payload.get("entity") == "job" and payload.get("id") in self.model.jobs:
                        job = self.model.jobs[payload["id"]]
                        job["status"] = payload.get("to")
                        if payload.get("to") == "needs_attention":
                            job["needs_attention_reason"] = (payload.get("reason") or "").split(" ")[0] or None
                    if payload.get("entity") == "mission" or payload.get("to") == "queued":
                        self._wake.set()  # new jobs or a finished mission: refresh the lists now
                if event.get("type") in {"action_proposed", "question", "action_status", "question_answered"}:
                    self._wake.set()
                self.model.stream_note = ""
                self.render_all()
        except CliError as exc:
            self.model.error = exc.message
            self.render_all()
        except Exception as exc:  # noqa: BLE001 - one broken stream must not stop the dashboard
            self.model.error = f"stream {mission_id}: {type(exc).__name__}: {exc}"
            self.render_all()

    @work(exclusive=True, group="global-stream", exit_on_error=False)
    async def global_stream(self) -> None:
        try:
            async for message in sse.astream(self.api, "/stream"):
                if message.event in {
                    "mission_status",
                    "job_status",
                    "review_counts",
                    "connector_health",
                    "inference_health",
                }:
                    self._wake.set()
        except NotAvailable:
            self.model.stream_note = "live: per-mission streams"
        except CliError as exc:
            self.model.stream_note = f"global stream: {exc.message[:40]}"
        self.render_all()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state == WorkerState.ERROR and event.worker.error is not None:
            error = event.worker.error
            text = error.message if isinstance(error, CliError) else f"{type(error).__name__}: {error}"
            self.notify(text, severity="error", timeout=10)
            if event.worker.group == "poll":
                self.poll()  # keep the dashboard alive

    # ---------------------------------------------------------------- rendering

    def selected(self) -> Row | None:
        table = self.query_one("#agents", DataTable)
        if not self._rows or table.cursor_row is None or table.cursor_row >= len(self._rows):
            return None
        return self._rows[table.cursor_row]

    def render_all(self) -> None:
        if not self._ui_ready:
            return
        m = self.model
        self.query_one("#header", Static).update(m.header())
        table = self.query_one("#agents", DataTable)
        previous = self.selected()
        rows = m.rows()
        self._rows = rows
        table.clear()
        for r in rows:
            mark_style = state_style(
                {"approval": "waiting", "question": "waiting", "attention": "waiting"}.get(r.state, r.state)
            )
            agent = Text()
            agent.append("● ", style=mark_style if r.state != "idle" else "z.d")
            agent.append(r.agent)
            table.add_row(
                agent,
                Text(r.mission_title[:46], style="z.d" if r.mission_id is None else "z.text"),
                Text(r.state, style=mark_style),
                r.step,
                tokens(r.tokens) if r.tokens else "",
                Text(duration(since(r.started)), style="z.d"),
                key=r.key,
            )
        if previous is not None:
            for i, r in enumerate(rows):
                if r.key == previous.key:
                    table.move_cursor(row=i, animate=False)
                    break
        self.query_one("#connectors", Static).update(m.connectors_line())
        self.query_one("#review", Static).update(m.review_line())
        row = self.selected()
        title = Text(f" ── {row.agent if row else 'nothing selected'} · live ", style="z.d")
        title.append("─" * 60, style="z.d")
        self.query_one("#live-title", Static).update(title)
        self.query_one("#live", Static).update(Group(*m.live_lines(row)))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row = self.selected()
        title = Text(f" ── {row.agent if row else 'nothing selected'} · live ", style="z.d")
        title.append("─" * 60, style="z.d")
        self.query_one("#live-title", Static).update(title)
        self.query_one("#live", Static).update(Group(*self.model.live_lines(row)))

    # ---------------------------------------------------------------- actions

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_open()

    def action_open(self) -> None:
        row = self.selected()
        if row is None or row.mission_id is None:
            self.notify("This agent is idle.", timeout=3)
            return
        view = self.model.view(row.mission_id)
        self.push_screen(
            TextScreen(Group(mission_tree(view, expand=True), Text(""), Text("q or esc to close", style="z.d")))
        )

    def action_logs(self) -> None:
        row = self.selected()
        if row is None or row.mission_id is None:
            self.notify("This agent is idle.", timeout=3)
            return
        self.push_screen(TextScreen(Group(*self.model.live_lines(row, limit=500))))

    def action_help(self) -> None:
        self.push_screen(TextScreen(Text(HELP)))

    def action_refresh(self) -> None:
        self._wake.set()

    @work(group="decide", exit_on_error=False)
    async def action_approve(self) -> None:
        row = self.selected()
        await decisions.decide(self.api, ModalPresenter(self), mission_id=row.mission_id if row else None)
        self._wake.set()

    @work(group="control", exit_on_error=False)
    async def action_pause(self) -> None:
        row = self.selected()
        if row is None or row.mission_id is None:
            self.notify("Select a mission first.", timeout=3)
            return
        mission = self.model.missions.get(row.mission_id, {})
        paused = mission.get("status") == "paused"
        verb = "resume" if paused else "pause"
        choice = await self.push_screen_wait(
            ChoiceScreen(Text(f"{verb.title()} {mission.get('title', row.mission_id)}?"), [verb.title(), "Cancel"])
        )
        if choice != 0:
            return
        try:
            await self.api.post(f"/missions/{row.mission_id}/{verb}", {})
            self.notify(f"Mission {verb}d.", timeout=4)
        except CliError as exc:
            ModalPresenter(self).error(exc)
        self._wake.set()

    @work(group="control", exit_on_error=False)
    async def action_stop(self) -> None:
        row = self.selected()
        if row is None or row.mission_id is None:
            self.notify("Select a mission first.", timeout=3)
            return
        title = self.model.missions.get(row.mission_id, {}).get("title", row.mission_id)
        body = Text(f"Stop {title}? Its agents are stopped and pending writes are not applied.", style="z.err")
        choice = await self.push_screen_wait(ChoiceScreen(body, ["Stop the mission", "Keep it running"], danger=True))
        if choice != 0:
            return
        try:
            await self.api.post(f"/missions/{row.mission_id}/cancel", {"reason": "stopped from z top"})
            self.notify("Mission stopped.", timeout=4)
        except CliError as exc:
            ModalPresenter(self).error(exc)
        self._wake.set()

    @work(group="control", exit_on_error=False)
    async def action_take_over(self) -> None:
        row = self.selected()
        if row is None or row.job_id is None:
            self.notify("Select a running job first.", timeout=3)
            return
        try:
            session = await self.api.post(
                f"/missions/{row.mission_id}/take-over",
                {"job_id": row.job_id, "mode": "read_only"},
                feature="Take over (POST /missions/{id}/take-over)",
            )
        except CliError as exc:
            ModalPresenter(self).error(exc)
            return
        self.push_screen(
            TextScreen(
                Text(
                    f"Job paused. Its workspace ({session.get('mode')}) is open until {session.get('expires_at')}:\n"
                    f"{session.get('terminal_url')}\nEnd the take-over there to resume."
                )
            )
        )

    @work(group="prompt", exit_on_error=False)
    async def action_filter(self) -> None:
        text = await self.push_screen_wait(
            PromptScreen("Filter rows (agent, mission, state); empty clears", value=self.model.filter)
        )
        if text is not None:
            self.model.filter = text.strip()
            self.render_all()

    @work(group="prompt", exit_on_error=False)
    async def action_command(self) -> None:
        text = await self.push_screen_wait(PromptScreen(":", "review · refresh · filter <text> · theme <name> · quit"))
        if not text:
            return
        cmd, _, arg = text.strip().lstrip(":").partition(" ")
        if cmd in {"q", "quit", "exit"}:
            self.exit(0)
        elif cmd in {"r", "refresh"}:
            self._wake.set()
        elif cmd == "review":
            await decisions.decide(self.api, ModalPresenter(self))
            self._wake.set()
        elif cmd == "filter":
            self.model.filter = arg.strip()
            self.render_all()
        elif cmd == "theme":
            self.theme_name = apply_theme(self, arg.strip() or None)
            self.render_all()
        elif cmd == "help":
            self.action_help()
        else:
            self.notify(f"unknown command {cmd!r} (try :help)", severity="warning")

    async def action_quit(self) -> None:
        self.exit(0)

    async def on_unmount(self) -> None:
        for task in self.streams.values():
            task.cancel()
        await self.api.aclose()


def run_top(st: State, *, refresh_s: float = 5.0) -> int:
    st.api().get("/me")  # fail fast with a readable error (and exit code 4) before going full screen
    app = TopApp(st.async_api(), refresh_s=refresh_s, theme_name=st.setting("theme"))
    code = app.run()
    return int(code or 0)
