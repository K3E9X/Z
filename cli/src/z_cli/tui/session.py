"""`z`: the interactive session (docs/10 §1, design/mockups/CLI.html), a Textual app in inline mode.

Welcome box, transcript with a live tree per agent (from each mission's SSE stream), inline decisions with
numbered choices, an input that completes `@agents`, `#connectors` and `/commands`, `!` for a local shell
command that is never sent to agents, and a status line. Text typed while a started mission is still running
guides it (`POST /missions/{id}/messages`); `/new` starts another one.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from z_cli import __version__, credentials, sse
from z_cli import state as state_mod
from z_cli.client import Api, AsyncApi
from z_cli.config import state_dir
from z_cli.errors import CliError, NotAvailable
from z_cli.live import MissionView
from z_cli.render import mission_tree, status_text, tokens
from z_cli.theme import THEME_NAMES, rich_theme, state_style
from z_cli.tui import decisions
from z_cli.tui.common import Choice, apply_theme
from z_cli.tui.top import edit_with_suspend

MODES = ("propose", "read_only", "plan_first")
MODE_LABEL = {"propose": "propose mode", "read_only": "read-only mode", "plan_first": "plan first"}
SLASH: dict[str, str] = {
    "/agents": "Roster with status; pick one to inspect",
    "/missions": "Running and recent missions; switch focus",
    "/review": "Pending approvals and questions, one by one",
    "/playbooks": "List and run a playbook",
    "/connectors": "Health and latency; /connectors test <name>",
    "/usage": "Tokens today by model and agent; budget left",
    "/model": "Override the model alias for this session (/model z-heavy, /model off)",
    "/autonomy": "propose or read_only for missions started here",
    "/plan": "Plan first: draft the plan and wait for you before running",
    "/theme": "night, day, slate, contrast",
    "/new": "Start a new mission even while one is focused",
    "/guide": "Guide the focused mission (/guide <text>)",
    "/stop": "Pause or stop the focused mission",
    "/login": "Sign in (device flow)",
    "/logout": "Sign out",
    "/whoami": "Who you are, teams, roles",
    "/resume": "Pick a past session",
    "/export": "Save the transcript as Markdown (/export [path])",
    "/clear": "Clear the transcript",
    "/help": "Everything",
    "/quit": "Leave",
}
KEYS_HELP = [
    ("Enter", "send"),
    ("Shift+Enter / Ctrl+J", "newline"),
    ("Esc", "interrupt the focused agent (asks first)"),
    ("Shift+Tab", "cycle mode: propose → read_only → plan first"),
    ("Ctrl+R", "search history"),
    ("Ctrl+O", "expand the last tool output"),
    ("Ctrl+T", "toggle the task list"),
    ("↑ ↓", "history, or move in completions"),
    ("Tab", "accept a completion"),
    ("@ # /", "complete agents, connectors, commands"),
    ("!cmd", "run a local shell command (never sent to agents)"),
    ("?", "these shortcuts (on an empty input)"),
]
HISTORY_MAX = 500


def _ok(text: str) -> Text:
    t = Text("● ", style="z.ok")
    t.append(text)
    return t


def _err(exc: CliError) -> Text:
    t = Text("✕ ", style="z.err")
    t.append(exc.message)
    if exc.hint:
        t.append(f"\n  {exc.hint}", style="z.d")
    return t


class PromptArea(TextArea):
    """Enter sends, Shift+Enter (or Ctrl+J) adds a newline; history and completion keys go to the app."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Nav(Message):
        def __init__(self, key: str) -> None:
            super().__init__()
            self.key = key

    async def _on_key(self, event: events.Key) -> None:
        app = self.app
        key = event.key
        completing = isinstance(app, SessionApp) and app.completion_open
        if key == "enter":
            event.stop()
            event.prevent_default()
            if completing:
                self.post_message(self.Nav("enter"))
                return
            self.post_message(self.Submitted(self.text))
            return
        if key in {"shift+enter", "ctrl+j"}:
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if key == "tab" and completing:
            event.stop()
            event.prevent_default()
            self.post_message(self.Nav("accept"))
            return
        if key in {"up", "down"} and (completing or "\n" not in self.text):
            event.stop()
            event.prevent_default()
            self.post_message(self.Nav(key))
            return
        if event.character == "?" and not self.text:
            event.stop()
            event.prevent_default()
            self.post_message(self.Nav("help"))
            return
        await super()._on_key(event)


class MissionBlock(Static):
    """The live tree of one mission."""

    def __init__(self, view: MissionView) -> None:
        super().__init__(classes="mission")
        self.view = view
        self.expand = False

    def redraw(self) -> None:
        self.update(mission_tree(self.view, expand=self.expand))


class InlinePresenter:
    """Decisions rendered as boxed prompts in the transcript."""

    def __init__(self, app: SessionApp) -> None:
        self.app = app

    async def choose(self, body: RenderableType | None, options: list[str], *, danger: bool = False) -> int | None:
        return await self.app.inline_choice(body, options, danger=danger)

    async def ask_text(self, title: str, placeholder: str = "") -> str | None:
        return await self.app.inline_text(title, placeholder)

    async def edit_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return edit_with_suspend(self.app, payload)

    def info(self, text: RenderableType) -> None:
        self.app.say(text)

    def error(self, exc: CliError) -> None:
        self.app.say(_err(exc))


class SessionApp(App[int]):
    CSS = """
    Screen { height: auto; background: $background; }
    #transcript { height: auto; max-height: 40; padding: 0 1; scrollbar-size-vertical: 1; }
    #transcript > Static { height: auto; margin: 0 0 1 0; }
    #transcript > .mission { margin: 0 0 1 0; }
    #transcript > Input { margin: 1 0 0 0; border: round $warning; }
    #tasks { height: auto; display: none; padding: 0 1; border: round $panel; margin: 0 1; }
    #tasks.on { display: block; }
    #complete { height: auto; max-height: 9; display: none; margin: 0 1; border: round $panel; }
    #complete.on { display: block; }
    #input { height: auto; min-height: 3; max-height: 10; margin: 0 1; border: round $panel; }
    #input:focus { border: round $secondary; }
    #status { height: 1; padding: 0 2; color: $text-muted; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "escape", "interrupt", show=False, priority=True),
        Binding("shift+tab", "cycle_mode", "mode", show=False, priority=True),
        Binding("ctrl+r", "history_search", "history", show=False, priority=True),
        Binding("ctrl+o", "expand", "expand", show=False, priority=True),
        Binding("ctrl+t", "tasks", "tasks", show=False, priority=True),
        Binding("ctrl+c", "quit", "quit", show=False, priority=True),
        Binding("ctrl+d", "quit", "quit", show=False),
    ]

    def __init__(self, st: state_mod.State, api: AsyncApi | None, *, cwd: Path | None = None) -> None:
        super().__init__()
        self.st = st
        self.api = api
        self.cwd = cwd or Path.cwd()
        default = st.setting("autonomy_default") or "propose"
        self.mode = default if default in MODES else "propose"
        self.model_override: str | None = None
        self.focus_id: str | None = None
        self.blocks: dict[str, MissionBlock] = {}
        self.streams: dict[str, asyncio.Task[None]] = {}
        self.offered: set[str] = set()
        self.deciding = asyncio.Lock()
        self.agents: list[dict[str, Any]] = []
        self.connectors: list[dict[str, Any]] = []
        self.playbooks: list[dict[str, Any]] = []
        self.me: dict[str, Any] | None = None
        self.counts = {"agents": 0, "decisions": 0, "tokens": 0}
        self.history: list[str] = self._load_history()
        self.history_pos: int | None = None
        self.completion_open = False
        self._completion_items: list[str] = []
        self._completion_mode = "complete"  # or "history"
        self._futures: dict[int, asyncio.Future[Any]] = {}
        self.markdown: list[str] = []
        self.theme_name = st.setting("theme")

    # ---------------------------------------------------------------- layout

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="transcript"):
            yield Static(self.welcome(), id="welcome")
        yield Static(id="tasks")
        yield OptionList(id="complete")
        yield PromptArea(id="input", soft_wrap=True, show_line_numbers=False, compact=True)
        yield Static(id="status")

    async def on_mount(self) -> None:
        self.theme_name = apply_theme(self, self.theme_name)
        area = self.query_one("#input", PromptArea)
        area.border_title = None
        area.focus()
        self.update_status()
        self.load_context()
        self.counts_loop()

    def welcome(self) -> Panel:
        body = Text()
        body.append("✻ ", style="z.acc")
        body.append("Welcome to Z", style="z.b")
        body.append(f"  v{__version__}", style="z.d")
        me = self.me or {}
        ws = me.get("workspace") or {}
        teams = ", ".join(t["name"] for t in ((me.get("user") or {}).get("teams") or []))
        where = [
            x
            for x in (teams or ws.get("name"), ws.get("environment"), str(self.cwd).replace(str(Path.home()), "~"))
            if x
        ]
        body.append("\n  " + " · ".join(where), style="z.d")
        if me.get("user"):
            body.append(f"\n  signed in as {me['user']['username']}", style="z.d")
            body.append(f" · {self.st.server_url()}", style="z.d")
        elif self.api is None:
            body.append("\n  not signed in: type /login", style="z.wait")
        inf = getattr(self, "inference", None)
        body.append("\n  inferX ", style="z.d")
        if isinstance(inf, dict):
            healthy = inf.get("status") == "healthy"
            body.append("●", style="z.ok" if healthy else "z.wait")
            aliases = " · ".join(m["alias"] for m in inf.get("models") or [])
            body.append(f" {inf.get('status')}" + (f" · {aliases}" if aliases else ""), style="z.d")
        else:
            body.append("?", style="z.d")
            body.append(f" {inf or 'checking…'}", style="z.d")
        return Panel(body, expand=False, border_style="z.d", padding=(0, 1))

    def update_status(self) -> None:
        t = Text("⏵⏵ ", style="z.d")
        t.append(MODE_LABEL[self.mode], style="z.wait" if self.mode != "propose" else "z.d")
        t.append(" (shift+tab)", style="z.d")
        if self.model_override:
            t.append(f"  ·  model {self.model_override}", style="z.d")
        t.append(f"  ·  {self.counts['agents']} agents running", style="z.d")
        n = self.counts["decisions"]
        t.append(f"  ·  {n} decisions", style="z.wait" if n else "z.d")
        t.append(f"  ·  {tokens(self.counts['tokens'])} tokens today", style="z.d")
        focus = self.blocks.get(self.focus_id or "")
        if focus is not None and not focus.view.terminal:
            t.append(f"  ·  guiding {focus.view.title[:30]}", style="z.run")
            t.append("  ·  esc to interrupt", style="z.d")
        else:
            t.append("  ·  ? for shortcuts", style="z.d")
        with contextlib.suppress(Exception):
            self.query_one("#status", Static).update(t)

    # ---------------------------------------------------------------- transcript

    def say(self, renderable: RenderableType, markdown: str | None = None) -> Static:
        widget = Static(renderable)
        self.query_one("#transcript", VerticalScroll).mount(widget)
        self.call_after_refresh(self._scroll_end)
        if markdown is not None:
            self.markdown.append(markdown)
        elif isinstance(renderable, Text):
            self.markdown.append(renderable.plain)
        return widget

    def _scroll_end(self) -> None:
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    async def inline_choice(
        self, body: RenderableType | None, options: list[str], *, danger: bool = False
    ) -> int | None:
        choice = Choice(body, options, danger=danger)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._futures[id(choice)] = future
        await self.query_one("#transcript", VerticalScroll).mount(choice)
        choice.focus()
        self.call_after_refresh(self._scroll_end)
        try:
            index: int | None = await future
        finally:
            self._futures.pop(id(choice), None)
            await choice.remove()
            self.query_one("#input", PromptArea).focus()
        if index is not None:
            picked = Text("  ⎿ ", style="z.d")
            picked.append(options[index], style="z.m")
            self.say(picked)
        return index

    def on_choice_chosen(self, message: Choice.Chosen) -> None:
        message.stop()
        future = self._futures.get(id(message.choice))
        if future is not None and not future.done():
            future.set_result(message.index)

    async def inline_text(self, title: str, placeholder: str = "") -> str | None:
        self.say(Text(title, style="z.wait"))
        field = Input(placeholder=placeholder, classes="inline-text")
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._futures[id(field)] = future
        await self.query_one("#transcript", VerticalScroll).mount(field)
        field.focus()
        self.call_after_refresh(self._scroll_end)
        try:
            value: str | None = await future
        finally:
            self._futures.pop(id(field), None)
            await field.remove()
            self.query_one("#input", PromptArea).focus()
        if value:
            self.say(Text(f"  ⎿ {value}", style="z.m"))
        return value

    def on_input_submitted(self, event: Input.Submitted) -> None:
        future = self._futures.get(id(event.input))
        if future is not None and not future.done():
            event.stop()
            future.set_result(event.value)

    # ---------------------------------------------------------------- context (catalog, health, counts)

    @work(exclusive=True, group="context")
    async def load_context(self) -> None:
        api = self.api
        if api is None:
            self.inference = "sign in to see health"
            self.query_one("#welcome", Static).update(self.welcome())
            return
        try:
            self.me = await api.get("/me")
            self.agents = await api.items("/agents", limit=200)
            self.connectors = list(await api.get("/connectors"))
            self.playbooks = await api.items("/playbooks", limit=200)
        except CliError as exc:
            self.say(_err(exc))
        try:
            self.inference = await api.get("/inference/health")
        except NotAvailable:
            self.inference = "health not served by this Z server yet"
        except CliError as exc:
            self.inference = exc.message
        self.query_one("#welcome", Static).update(self.welcome())

    @work(exclusive=True, group="counts")
    async def counts_loop(self) -> None:
        while True:
            await self.refresh_counts()
            await asyncio.sleep(10)

    async def refresh_counts(self) -> None:
        api = self.api
        if api is None:
            return
        try:
            active = await api.items("/missions", {"status": "queued,running,waiting,paused"}, limit=200)
            self.counts["agents"] = sum(int((m.get("jobs") or {}).get("running", 0)) for m in active)
            review = await api.get("/review", {"limit": 1})
            self.counts["decisions"] = int(review["counts"]["all"])
            self.counts["tokens"] = await self.tokens_today(api)
        except CliError:
            pass
        self.update_status()

    async def tokens_today(self, api: AsyncApi) -> int:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            usage = await api.get("/usage", {"group_by": "user", "from": start.isoformat()})
            return sum(int(s["tokens_in"]) + int(s["tokens_out"]) for s in usage.get("series", []))
        except NotAvailable:
            missions = await api.items("/missions", limit=200)
            total = 0
            for m in missions:
                created = m.get("created_at") or ""
                if created and datetime.fromisoformat(created.replace("Z", "+00:00")) >= start:
                    total += int(m.get("tokens_in", 0)) + int(m.get("tokens_out", 0))
            return total

    # ---------------------------------------------------------------- input

    async def on_prompt_area_submitted(self, message: PromptArea.Submitted) -> None:
        text = message.text.strip()
        area = self.query_one("#input", PromptArea)
        if not text:
            return
        area.clear()
        self.hide_completion()
        self._remember(text)
        await self.handle(text)

    async def handle(self, text: str) -> None:
        if text.startswith("!"):
            self.run_shell(text[1:].strip())
            return
        if text.startswith("/"):
            await self.slash(text)
            return
        focus = self.blocks.get(self.focus_id or "")
        if focus is not None and not focus.view.terminal:
            self.guide(focus.view.mission_id, text)
            return
        self.start_mission(text)

    def _echo_prompt(self, text: str) -> None:
        line = Text("> ", style="z.d")
        for i, row in enumerate(text.splitlines()):
            if i:
                line.append("\n  ")
            for word in row.split(" "):
                style = "z.run" if word.startswith(("@", "#")) and len(word) > 1 else "z.text"
                line.append(word, style=style)
                line.append(" ")
        self.say(line, markdown=f"> {text}")

    # ---------------------------------------------------------------- missions

    def _need_api(self) -> AsyncApi | None:
        if self.api is None:
            self.say(Text("Not signed in: type /login first.", style="z.wait"))
        return self.api

    @work(group="missions")
    async def start_mission(self, text: str) -> None:
        api = self._need_api()
        if api is None:
            return
        self._echo_prompt(text)
        from z_cli.commands.missions import mentions

        handles, conns = mentions(text)
        known = {a["handle"]: a for a in self.agents} | {a["name"]: a for a in self.agents}
        unknown = [h for h in handles if h not in known]
        if unknown:
            self.say(Text(f"Unknown agent {', '.join('@' + u for u in unknown)} (try /agents).", style="z.wait"))
            return
        if not handles:
            self.say(
                Text(
                    "Say which agent should do it with @handle, or run a playbook with /playbooks. "
                    "(Free-form planning across agents, POST /missions/draft-plan, is not available on this server yet.)",
                    style="z.wait",
                )
            )
            return
        body: dict[str, Any] = {"prompt": text}
        if self.model_override and len(handles) == 1:
            agent = known[handles[0]]
            body["plan"] = {
                "steps": [{"id": "main", "agent": agent["name"], "prompt": text, "model": self.model_override}]
            }
            body["title"] = text[:200]
        else:
            body["agents"] = [known[h]["handle"] for h in handles]
        if conns:
            body["connectors"] = conns
        body["autonomy"] = "read_only" if self.mode == "read_only" else "propose"
        if self.mode == "plan_first":
            body["plan_first"] = True
        try:
            mission = await api.post("/missions", body)
        except CliError as exc:
            self.say(_err(exc))
            return
        self.attach(mission)

    def attach(self, mission: dict[str, Any]) -> None:
        mid = mission["id"]
        if mid in self.blocks:
            self.focus_id = mid
            self.update_status()
            return
        view = MissionView(mid)
        view.load_mission(mission)
        view.use_agents(self.agents)
        block = MissionBlock(view)
        self.blocks[mid] = block
        self.query_one("#transcript", VerticalScroll).mount(block)
        block.redraw()
        self.focus_id = mid
        self.call_after_refresh(self._scroll_end)
        self.streams[mid] = asyncio.create_task(self.follow(mid), name=f"sse-{mid}")
        self.update_status()
        self.update_tasks()

    async def follow(self, mission_id: str) -> None:
        api = self.api
        assert api is not None
        block = self.blocks[mission_id]
        view = block.view

        async def finished() -> bool:
            try:
                mission = await api.get(f"/missions/{mission_id}")
            except CliError:
                return False
            view.load_mission(mission)
            return view.terminal

        try:
            async for message in sse.astream(api, f"/missions/{mission_id}/events", finished=finished):
                event = message.json()
                if not isinstance(event, dict):
                    continue
                view.apply(event)
                block.redraw()
                self.update_tasks()
                if view.terminal and self.focus_id == mission_id:
                    self.focus_id = None  # the next request starts a new mission
                    self.update_status()
                if event.get("type") in {"action_proposed", "question"} or (
                    event.get("type") == "status" and (event.get("payload") or {}).get("to") in {"waiting", "planned"}
                ):
                    self.offer_decisions(mission_id)
        except CliError as exc:
            self.say(_err(exc))
        block.redraw()
        if view.terminal:
            self.markdown.append(_plain(mission_tree(view)))
            if self.focus_id == mission_id:
                self.focus_id = None
            self.update_status()
            await self.refresh_counts()

    @work(group="decisions")
    async def offer_decisions(self, mission_id: str) -> None:
        api = self.api
        if api is None:
            return
        async with self.deciding:
            view = self.blocks[mission_id].view
            try:
                mission = await api.get(f"/missions/{mission_id}")
            except CliError as exc:
                self.say(_err(exc))
                return
            view.load_mission(mission)
            if (
                mission["status"] == "planned"
                and mission.get("plan_first")
                and f"plan:{mission_id}" not in self.offered
            ):
                self.offered.add(f"plan:{mission_id}")
                idx = await self.inline_choice(
                    Group(
                        Text("The plan is ready and waits for you.", style="z.wait"),
                        mission_tree(view, show_jobs=False),
                    ),
                    ["Start the mission", "Cancel it", "Not now"],
                )
                if idx == 0:
                    await self._control(mission_id, "start")
                elif idx == 1:
                    await self._control(mission_id, "cancel")
                return
            review = await api.get("/review", {"mission": mission_id, "limit": 200})
            ids = {(i.get("action") or i.get("question") or {}).get("id") for i in review["items"]}
            fresh = {i for i in ids if i and i not in self.offered}
            if not fresh:
                return
            self.offered |= fresh
            await decisions.decide(api, InlinePresenter(self), mission_id=mission_id)
            await self.refresh_counts()

    @work(group="missions")
    async def guide(self, mission_id: str, text: str) -> None:
        api = self._need_api()
        if api is None:
            return
        line = Text("> ", style="z.d")
        line.append(text)
        line.append(f"   → guiding {self.blocks[mission_id].view.title[:40]}", style="z.d")
        self.say(line, markdown=f"> {text} (guidance)")
        try:
            await api.post(f"/missions/{mission_id}/messages", {"text": text})
        except CliError as exc:
            self.say(_err(exc))

    async def _control(self, mission_id: str, verb: str) -> None:
        api = self.api
        if api is None:
            return
        try:
            mission = await api.post(f"/missions/{mission_id}/{verb}", {"reason": f"{verb} from the z session"})
        except CliError as exc:
            self.say(_err(exc))
            return
        line = Text("● ", style=state_style(mission["status"]))
        line.append(f"{mission['title'][:60]} ")
        line.append_text(status_text(mission["status"], mission.get("waiting_reason")))
        self.say(line)

    # ---------------------------------------------------------------- shell

    @work(group="shell")
    async def run_shell(self, command: str) -> None:
        if not command:
            return
        head = Text("! ", style="z.acc")
        head.append(command)
        head.append("   (local shell, not sent to agents)", style="z.d")
        if command == "cd" or command.startswith("cd "):
            target = Path(os.path.expanduser(command[2:].strip() or "~"))
            target = (self.cwd / target).resolve() if not target.is_absolute() else target
            if target.is_dir():
                self.cwd = target
                self.say(Group(head, Text(f"  {self.cwd}", style="z.d")), markdown=f"$ {command}")
            else:
                self.say(Group(head, Text(f"  no such directory: {target}", style="z.err")))
            return
        proc = await asyncio.create_subprocess_shell(
            command, cwd=self.cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await proc.communicate()
        text = out.decode(errors="replace").rstrip("\n")
        lines = text.splitlines()
        shown = lines[-200:]
        body = Text("\n".join("  " + x for x in shown), style="z.m")
        if len(lines) > len(shown):
            body = Text(f"  … {len(lines) - len(shown)} lines above\n", style="z.d") + body
        status = Text(f"  exit {proc.returncode}", style="z.d" if proc.returncode == 0 else "z.err")
        self.say(Group(head, body, status) if text else Group(head, status), markdown=f"$ {command}\n{text}")

    # ---------------------------------------------------------------- slash commands

    async def slash(self, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        arg = arg.strip()
        handlers: dict[str, Callable[[str], Awaitable[None]]] = {
            "/help": self.cmd_help,
            "/agents": self.cmd_agents,
            "/missions": self.cmd_missions,
            "/review": self.cmd_review,
            "/playbooks": self.cmd_playbooks,
            "/connectors": self.cmd_connectors,
            "/usage": self.cmd_usage,
            "/model": self.cmd_model,
            "/autonomy": self.cmd_autonomy,
            "/plan": self.cmd_plan,
            "/theme": self.cmd_theme,
            "/new": self.cmd_new,
            "/guide": self.cmd_guide,
            "/stop": self.cmd_stop,
            "/login": self.cmd_login,
            "/logout": self.cmd_logout,
            "/whoami": self.cmd_whoami,
            "/resume": self.cmd_resume,
            "/export": self.cmd_export,
            "/clear": self.cmd_clear,
            "/quit": self.cmd_quit,
            "/exit": self.cmd_quit,
        }
        handler = handlers.get(cmd)
        if handler is None:
            self.say(Text(f"Unknown command {cmd} (type /help).", style="z.wait"))
            return
        self.say(Text(text, style="z.d"))
        self.run_worker(handler(arg), group="slash", exclusive=False)

    async def cmd_help(self, arg: str) -> None:
        rows = Text()
        for name, desc in SLASH.items():
            rows.append(f"  {name:<12}", style="z.b")
            rows.append(f" {desc}\n", style="z.d")
        rows.append("\n")
        for key, desc in KEYS_HELP:
            rows.append(f"  {key:<22}", style="z.b")
            rows.append(f" {desc}\n", style="z.d")
        rows.append(
            "\n  Type a request with an @agent to start a mission; while it runs, what you type guides it.", style="z.d"
        )
        self.say(rows)

    async def cmd_agents(self, arg: str) -> None:
        api = self._need_api()
        if api is None:
            return
        try:
            self.agents = await api.items("/agents", limit=200)
        except CliError as exc:
            self.say(_err(exc))
            return
        if arg:
            name = arg.removeprefix("@")
            agent = next((a for a in self.agents if name in (a["handle"], a["name"])), None)
            if agent is None:
                self.say(Text(f"No agent @{name}.", style="z.wait"))
                return
            self.say(_agent_card(agent))
            return
        options = [f"{a['display_name']}  @{a['handle']}  · {a['status']} · {a['model']}" for a in self.agents]
        idx = await self.inline_choice(Text("Agents", style="z.b"), [*options, "Close"])
        if idx is not None and idx < len(self.agents):
            self.say(_agent_card(self.agents[idx]))

    async def cmd_missions(self, arg: str) -> None:
        api = self._need_api()
        if api is None:
            return
        try:
            missions = await api.items("/missions", limit=15)
        except CliError as exc:
            self.say(_err(exc))
            return
        if not missions:
            self.say(Text("No missions yet.", style="z.d"))
            return
        options = [f"{m['title'][:50]}  · {m['status']} · {m['id']}" for m in missions]
        idx = await self.inline_choice(Text("Missions (pick one to focus)", style="z.b"), [*options, "Close"])
        if idx is not None and idx < len(missions):
            self.attach(missions[idx])

    async def cmd_review(self, arg: str) -> None:
        api = self._need_api()
        if api is None:
            return
        async with self.deciding:
            await decisions.decide(api, InlinePresenter(self), mission_id=arg or None)
        await self.refresh_counts()

    async def cmd_playbooks(self, arg: str) -> None:
        api = self._need_api()
        if api is None:
            return
        try:
            self.playbooks = await api.items("/playbooks", limit=200)
        except CliError as exc:
            self.say(_err(exc))
            return
        playbook = next((p for p in self.playbooks if p["name"] == arg), None) if arg else None
        if playbook is None:
            options = [f"{p['name']}  v{p['version']} · {p['description'][:50]}" for p in self.playbooks]
            idx = await self.inline_choice(Text("Playbooks (pick one to run)", style="z.b"), [*options, "Close"])
            if idx is None or idx >= len(self.playbooks):
                return
            playbook = self.playbooks[idx]
        schema = (playbook.get("inputs") or {}).get("schema") or {}
        inputs: dict[str, Any] = {}
        from z_cli.missions import parse_inputs

        for name in schema.get("required") or []:
            prop = (schema.get("properties") or {}).get(name, {})
            value = await self.inline_text(
                f"{playbook['name']}: {name} ({prop.get('description') or prop.get('type', 'string')})"
            )
            if value is None:
                self.say(Text("Not started.", style="z.d"))
                return
            try:
                inputs |= parse_inputs([f"{name}={value}"], schema)
            except CliError as exc:
                self.say(_err(exc))
                return
        body: dict[str, Any] = {"inputs": inputs}
        if self.mode == "plan_first":
            body["plan_first"] = True
        self._echo_prompt(f"/playbooks {playbook['name']} " + " ".join(f"{k}={v}" for k, v in inputs.items()))
        try:
            mission = await api.post(f"/playbooks/{playbook['name']}/run", body)
        except CliError as exc:
            self.say(_err(exc))
            return
        self.attach(mission)

    async def cmd_connectors(self, arg: str) -> None:
        api = self._need_api()
        if api is None:
            return
        if arg.startswith("test"):
            name = arg.removeprefix("test").strip()
            try:
                result = await api.post(
                    f"/connectors/{name}/test", {}, feature="Connector tests (POST /connectors/{id}/test)"
                )
            except CliError as exc:
                self.say(_err(exc))
                return
            self.say(Text(json.dumps(result, indent=2, default=str)))
            return
        try:
            self.connectors = list(await api.get("/connectors"))
        except CliError as exc:
            self.say(_err(exc))
            return
        from z_cli.commands.catalog import health_text

        rows = [Text("Connectors", style="z.b")]
        for c in self.connectors:
            line = Text(f"  {c['name']:<14}", style="z.text")
            line.append_text(health_text(c.get("health") or {}))
            line.append(
                f"   {c['operations_exposed']}/{c['operations_total']} exposed · {c['review_status']}", style="z.d"
            )
            rows.append(line)
        self.say(Group(*rows))

    async def cmd_usage(self, arg: str) -> None:
        api = self._need_api()
        if api is None:
            return
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        rows: list[RenderableType] = []
        for group in ("model", "agent"):
            try:
                usage = await api.get("/usage", {"group_by": group, "from": start}, feature="Usage (GET /usage)")
            except CliError as exc:
                self.say(_err(exc))
                return
            rows.append(Text(f"Tokens today by {group}", style="z.b"))
            for s in usage.get("series", []):
                line = Text(f"  {s.get('label') or s['key']:<20} {tokens(int(s['tokens_in']) + int(s['tokens_out']))}")
                if s.get("cap"):
                    line.append(f" of {tokens(s['cap'])}", style="z.d")
                rows.append(line)
        self.say(Group(*rows))

    async def cmd_model(self, arg: str) -> None:
        if not arg:
            self.say(
                Text(f"Model override: {self.model_override or 'none (each agent uses its own model)'}", style="z.d")
            )
            return
        if arg in {"off", "none", "default"}:
            self.model_override = None
        elif arg.startswith(("z-", "ext-")):
            self.model_override = arg
        else:
            self.say(Text("Model aliases look like z-heavy, z-fast or ext-… (/model off to clear).", style="z.wait"))
            return
        self.say(
            _ok(
                f"Model for missions started here: {self.model_override or 'agent default'}"
                + (" (applies to @agent requests; playbooks keep their own models)" if self.model_override else "")
            )
        )
        self.update_status()

    async def cmd_autonomy(self, arg: str) -> None:
        if arg not in {"propose", "read_only"}:
            self.say(Text("Use /autonomy propose or /autonomy read_only.", style="z.wait"))
            return
        self.mode = arg
        self.say(_ok(f"Autonomy for missions started here: {arg}"))
        self.update_status()

    async def cmd_plan(self, arg: str) -> None:
        self.mode = "plan_first"
        self.say(_ok("Plan first: Z drafts the plan and waits for you; nothing runs until you accept."))
        self.update_status()

    async def cmd_theme(self, arg: str) -> None:
        if arg not in THEME_NAMES:
            self.say(Text(f"Themes: {', '.join(THEME_NAMES)}", style="z.wait"))
            return
        self.theme_name = apply_theme(self, arg)
        with contextlib.suppress(CliError):
            self.st.config.set("theme", arg, self.st.profile)
            self.st.config.save()
        for block in self.blocks.values():
            block.redraw()
        self.query_one("#welcome", Static).update(self.welcome())
        self.say(_ok(f"Theme {arg}"))

    async def cmd_new(self, arg: str) -> None:
        if not arg:
            self.focus_id = None
            self.update_status()
            self.say(Text("Your next request starts a new mission.", style="z.d"))
            return
        self.focus_id = None
        self.start_mission(arg)

    async def cmd_guide(self, arg: str) -> None:
        focus = self.blocks.get(self.focus_id or "")
        if focus is None or focus.view.terminal or not arg:
            self.say(Text("Nothing to guide: focus a running mission (/missions) and give the text.", style="z.wait"))
            return
        self.guide(focus.view.mission_id, arg)

    async def cmd_stop(self, arg: str) -> None:
        await self.interrupt()

    async def cmd_login(self, arg: str) -> None:
        from z_cli import auth

        try:
            url = self.st.server_url()
        except CliError as exc:
            self.say(_err(exc))
            return
        sync_api = Api(url, None, transport=self.st.transport)

        def notify(text: str) -> None:
            self.call_from_thread(self.say, Text(text, style="z.m"))

        try:
            result = await asyncio.to_thread(auth.login, sync_api, notify)
        except CliError as exc:
            self.say(_err(exc))
            return
        finally:
            sync_api.close()
        credentials.save(url, str(result["token"]))
        if self.api is not None:
            await self.api.aclose()
        self.api = AsyncApi(url, str(result["token"]))
        self.say(_ok(f"Signed in as {result['user']['username']}"))
        self.load_context()

    async def cmd_logout(self, arg: str) -> None:
        api = self.api
        url = self.st.server_url()
        if api is not None:
            with contextlib.suppress(CliError):
                await api.post("/auth/logout")
            await api.aclose()
        credentials.forget(url)
        self.api = None
        self.me = None
        self.say(_ok(f"Signed out of {url}"))
        self.query_one("#welcome", Static).update(self.welcome())

    async def cmd_whoami(self, arg: str) -> None:
        api = self._need_api()
        if api is None:
            return
        try:
            me = await api.get("/me")
        except CliError as exc:
            self.say(_err(exc))
            return
        user = me["user"]
        line = Text(f"{user['name']}  @{user['username']}", style="z.b")
        teams = ", ".join(t["name"] for t in user.get("teams") or []) or "no team"
        roles = ", ".join(f"{r['role']} ({r['scope']})" for r in user.get("effective_roles") or []) or "no role"
        self.say(
            Group(
                line,
                Text(f"  {teams} · {roles}", style="z.d"),
                Text(f"  session until {me['session']['expires_at']}", style="z.d"),
            )
        )

    async def cmd_resume(self, arg: str) -> None:
        api = self._need_api()
        if api is None:
            return
        try:
            await api.get("/sessions", {"surface": "cli"}, feature="Saved sessions (GET /sessions)")
        except CliError as exc:
            self.say(_err(exc))

    async def cmd_export(self, arg: str) -> None:
        path = Path(arg).expanduser() if arg else self.cwd / f"z-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        if not path.is_absolute():
            path = self.cwd / path
        lines = [f"# Z session · {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
        for entry in self.markdown:
            lines += [entry, ""]
        for block in self.blocks.values():
            if not block.view.terminal:
                lines += ["```", _plain(mission_tree(block.view)), "```", ""]
        try:
            path.write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            self.say(Text(f"Cannot write {path}: {exc}", style="z.err"))
            return
        self.say(_ok(f"Transcript saved to {path}"))

    async def cmd_clear(self, arg: str) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        for child in list(transcript.children):
            if child.id != "welcome" and not isinstance(child, Choice | Input):
                if isinstance(child, MissionBlock) and not child.view.terminal:
                    continue
                await child.remove()

    async def cmd_quit(self, arg: str) -> None:
        self.exit(0)

    # ---------------------------------------------------------------- keys

    async def action_escape(self) -> None:
        focused = self.focused
        if isinstance(focused, Choice):
            focused.action_dismiss()
            return
        if isinstance(focused, Input) and id(focused) in self._futures:
            future = self._futures[id(focused)]
            if not future.done():
                future.set_result(None)
            return
        if self.completion_open:
            self.hide_completion()
            return
        self.run_worker(self.interrupt(), group="interrupt", exclusive=True)

    async def interrupt(self) -> None:
        focus = self.blocks.get(self.focus_id or "")
        if focus is None or focus.view.terminal:
            self.say(Text("Nothing running in focus.", style="z.d"))
            return
        view = focus.view
        agents = ", ".join(sorted({f"@{j.agent}" for j in view.jobs.values() if j.active})) or view.title[:40]
        idx = await self.inline_choice(
            Text(f"Interrupt {agents}?", style="z.wait"),
            ["Pause the mission (resume later)", "Stop the mission", "Keep running"],
            danger=True,
        )
        if idx == 0:
            await self._control(view.mission_id, "pause")
        elif idx == 1:
            await self._control(view.mission_id, "cancel")

    def action_cycle_mode(self) -> None:
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        self.update_status()

    def action_expand(self) -> None:
        block = self.blocks.get(self.focus_id or "") or (list(self.blocks.values())[-1] if self.blocks else None)
        if block is None:
            return
        block.expand = not block.expand
        block.redraw()

    def action_tasks(self) -> None:
        self.query_one("#tasks", Static).toggle_class("on")
        self.update_tasks()

    def update_tasks(self) -> None:
        panel = self.query_one("#tasks", Static)
        if not panel.has_class("on"):
            return
        rows: list[RenderableType] = [Text("Tasks", style="z.b")]
        for block in self.blocks.values():
            view = block.view
            head = Text("● ", style=state_style(view.status))
            head.append(view.title[:60])
            head.append(f"  {view.status}", style=state_style(view.status))
            rows.append(head)
            from z_cli.render import mark

            for step in view.steps.values():
                glyph, style = mark(view.step_status(step))
                t = Text(f"   {glyph} ", style=style)
                t.append(step.title[:70])
                rows.append(t)
        if len(rows) == 1:
            rows.append(Text("  nothing yet", style="z.d"))
        panel.update(Group(*rows))

    def action_history_search(self) -> None:
        area = self.query_one("#input", PromptArea)
        needle = area.text.strip().lower()
        items = [h for h in reversed(self.history) if needle in h.lower()][:8]
        if not items:
            self.notify("No matching history.", timeout=2)
            return
        self.show_completion(items, mode="history")

    async def action_quit(self) -> None:
        self.exit(0)

    # ---------------------------------------------------------------- completion and history

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "input":
            return
        text = event.text_area.text
        _, _, word = self._token(event.text_area)
        if word.startswith("/"):
            items = [c for c in SLASH if c.startswith(word)]
        elif word.startswith("@"):
            items = [f"@{a['handle']}" for a in self.agents if a["handle"].startswith(word[1:])]
        elif word.startswith("#"):
            items = [f"#{c['name']}" for c in self.connectors if c["name"].startswith(word[1:])]
        else:
            items = []
        if items and items != [word] and text:
            self.show_completion(items[:8])
        elif self._completion_mode == "complete":
            self.hide_completion()

    @staticmethod
    def _token(area: TextArea) -> tuple[int, int, str]:
        """(row, start column, text) of the word being typed; a leading /command is the whole first word."""
        row, col = area.cursor_location
        lines = area.text.split("\n")
        line = lines[row] if row < len(lines) else ""
        before = line[:col]
        if row == 0 and line.startswith("/") and " " not in before:
            return row, 0, before
        word = before.split(" ")[-1]
        return row, col - len(word), word

    def show_completion(self, items: list[str], mode: str = "complete") -> None:
        box = self.query_one("#complete", OptionList)
        box.clear_options()
        box.add_options([Option(Text(i)) for i in items])
        box.highlighted = 0
        box.add_class("on")
        self._completion_items = items
        self._completion_mode = mode
        self.completion_open = True

    def hide_completion(self) -> None:
        box = self.query_one("#complete", OptionList)
        box.remove_class("on")
        self.completion_open = False
        self._completion_mode = "complete"

    def on_prompt_area_nav(self, message: PromptArea.Nav) -> None:
        area = self.query_one("#input", PromptArea)
        box = self.query_one("#complete", OptionList)
        if message.key == "help":
            self.run_worker(self.cmd_help(""), group="slash")
            return
        if self.completion_open:
            if message.key in {"up", "down"}:
                n = len(self._completion_items)
                current = box.highlighted or 0
                box.highlighted = (current + (1 if message.key == "down" else -1)) % n
                return
            if message.key in {"accept", "enter"}:
                choice = self._completion_items[box.highlighted or 0]
                if self._completion_mode == "history":
                    self.hide_completion()
                    area.load_text(choice)
                    area.cursor_location = area.document.end
                    return
                row, start, word = self._token(area)
                self.hide_completion()
                if message.key == "enter" and word == choice:
                    area.post_message(PromptArea.Submitted(area.text))
                    return
                lines = area.text.split("\n")
                col = start + len(word)
                lines[row] = lines[row][:start] + choice + " " + lines[row][col:].lstrip()
                area.load_text("\n".join(lines))
                area.cursor_location = (row, start + len(choice) + 1)
                return
        if message.key in {"up", "down"} and self.history:
            if self.history_pos is None:
                self.history_pos = len(self.history)
            self.history_pos += -1 if message.key == "up" else 1
            self.history_pos = max(0, min(len(self.history), self.history_pos))
            area.load_text(self.history[self.history_pos] if self.history_pos < len(self.history) else "")
            area.cursor_location = area.document.end

    def _history_file(self) -> Path:
        return state_dir() / "history"

    def _load_history(self) -> list[str]:
        try:
            lines = self._history_file().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines[-HISTORY_MAX:]:
            with contextlib.suppress(ValueError):
                value = json.loads(line)
                if isinstance(value, str):
                    out.append(value)
        return out

    def _remember(self, text: str) -> None:
        self.history_pos = None
        if self.history and self.history[-1] == text:
            return
        self.history.append(text)
        path = self._history_file()
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(text) + "\n")

    async def on_unmount(self) -> None:
        for task in self.streams.values():
            task.cancel()
        if self.api is not None:
            await self.api.aclose()


def _plain(renderable: RenderableType) -> str:
    console = Console(width=120, record=True, color_system=None, file=io.StringIO(), theme=rich_theme(None))
    console.print(renderable)
    return console.export_text().rstrip()


def _agent_card(agent: dict[str, Any]) -> Group:
    head = Text(f"{agent['display_name']}  @{agent['handle']}", style="z.b")
    head.append(f"  {agent['status']}", style=state_style(agent["status"]))
    limits = agent.get("limits") or {}
    return Group(
        head,
        Text(f"  {agent['role']}", style="z.m"),
        Text(
            f"  model {agent['model']} · autonomy {agent['autonomy']} · {limits.get('max_steps')} steps · "
            f"skills {', '.join(agent.get('skills') or []) or 'none'}",
            style="z.d",
        ),
    )


def run_session(st: state_mod.State) -> int:
    api: AsyncApi | None = None
    if credentials.load(st.server_url()) is not None:
        st.api().get("/me")  # fail fast (exit 4) on an expired token before opening the session
        api = st.async_api()
    app = SessionApp(st, api)
    code = app.run(inline=True, inline_no_clear=True)
    return int(code or 0)
