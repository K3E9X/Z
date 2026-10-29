"""The `z` command (docs/10): interactive session, `z top`, and subcommands for scripts and CI.

Exit codes: 0 success · 1 failure (mission failed, cancelled or timed out; request refused) · 2 usage error ·
3 needs attention (headless stops at a decision) · 4 auth (not signed in, expired, permission, step-up) ·
5 budget exceeded · 130 interrupted.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from z_cli import __version__, output, state
from z_cli.commands import account, admin, audit, catalog, misc, missions, work
from z_cli.commands._common import ask, command, guarded
from z_cli.config import Config
from z_cli.errors import NotAvailable, UsageError

app = typer.Typer(
    name="z",
    help="Z: run many AI agents in parallel against your tools, with a human approving every write.\n\n"
    "Run `z` alone for the interactive session, `z top` for the live dashboard.",
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=False,
    add_completion=True,
)

app.add_typer(missions.app, name="missions")
app.add_typer(work.jobs_app, name="jobs")
app.add_typer(work.review_app, name="review")
app.add_typer(catalog.agents_app, name="agents")
app.add_typer(catalog.skills_app, name="skills")
app.add_typer(catalog.playbooks_app, name="playbooks")
app.add_typer(catalog.connectors_app, name="connectors")
app.add_typer(audit.app, name="audit")
app.add_typer(admin.app, name="admin")
app.add_typer(misc.config_app, name="config")
app.add_typer(misc.inference_app, name="inference")
app.add_typer(account.tokens_app, name="tokens")
app.add_typer(account.sessions_app, name="sessions")
for group in (account.app, missions.top_level, work.top_level, misc.top_level):
    app.registered_commands.extend(group.registered_commands)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"z {__version__}")
        raise typer.Exit(0)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    prompt: Annotated[
        str | None,
        typer.Option("--print", "-p", help="Headless: run this prompt, print the result, exit with its code."),
    ] = None,
    playbook: Annotated[str | None, typer.Option("--playbook", help="With -p: run this playbook.")] = None,
    agent: Annotated[list[str] | None, typer.Option("--agent", "-a", help="With -p: the @agent to ask.")] = None,
    input_: Annotated[list[str] | None, typer.Option("--input", "-i", help="With -p --playbook: key=value.")] = None,
    output_format: Annotated[str, typer.Option("--output-format", help="With -p: text, json or stream-json.")] = "text",
    plan_first: Annotated[bool, typer.Option("--plan-first", help="With -p: draft the plan and stop.")] = False,
    continue_: Annotated[bool, typer.Option("--continue", "-c", help="Continue the last session.")] = False,
    resume: Annotated[bool, typer.Option("--resume", help="Pick a past session.")] = False,
    session: Annotated[str | None, typer.Option("--session", help="Open this saved session (ses_…).")] = None,
    url: Annotated[
        str | None, typer.Option("--url", envvar="Z_URL", help="Z server URL (overrides the profile).")
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", envvar="Z_PROFILE", help="Config profile.")] = None,
    theme: Annotated[str | None, typer.Option("--theme", help="night, day, slate or contrast.")] = None,
    no_color: Annotated[bool, typer.Option("--no-color", help="No colors (also NO_COLOR=1).")] = False,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Print the version.")
    ] = False,
) -> None:
    def setup() -> state.State:
        st = state.set_state(state.State(config=Config.load(), url_override=url, profile=profile))
        output.configure(theme or st.setting("theme"), no_color=no_color)
        return st

    st = guarded(setup)
    if ctx.invoked_subcommand is not None:
        return
    if prompt is not None:
        code = guarded(
            lambda: missions.headless(
                prompt,
                playbook=playbook,
                agents=agent or [],
                inputs=input_ or [],
                output_format=output_format,
                plan_first=plan_first,
            ),
            as_json=output_format in {"json", "stream-json"},
        )
        raise typer.Exit(int(code))
    session_id = session
    if session_id:  # fail here (exit 1) rather than inside the full-screen session
        guarded(lambda: state.get().api().get(f"/sessions/{session_id}", feature="Saved sessions"))
    if continue_ or resume:
        session_id = guarded(lambda: _pick_session(last=continue_))
        if session_id is None:
            return
    from z_cli.tui.session import run_session

    raise typer.Exit(int(guarded(lambda: run_session(st, session_id=session_id))))


def _pick_session(*, last: bool) -> str | None:
    """`z -c`: the latest CLI session. `z --resume`: list them and ask which one (in a terminal)."""
    try:
        page = state.get().api().get("/sessions", {"surface": "cli", "limit": 20}, feature="Saved sessions")
    except NotAvailable as exc:
        raise NotAvailable("Saved sessions (`z -c`, `z --resume`)") from exc
    rows = page.get("items") or []
    if not rows:
        output.note("No saved session yet: type a request in `z` to start one.")
        return None
    if last:
        return str(rows[0]["id"])
    output.out().print(
        output.table(
            ["#", "Session", "Title", "Missions", "Updated"],
            [
                [str(i), r["id"], r["title"], str(len(r["missions"])), r["updated_at"][:16].replace("T", " ")]
                for i, r in enumerate(rows, 1)
            ],
        )
    )
    if not sys.stdin.isatty():
        output.note("Open one with `z --session <id>`.")
        return None
    choice = ask("Session number", default="1").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(rows):
        raise UsageError(f"pick a number between 1 and {len(rows)}")
    return str(rows[int(choice) - 1]["id"])


@command(app, "top")
def top(
    refresh: Annotated[float, typer.Option("--refresh", min=1.0, help="Seconds between list refreshes.")] = 5.0,
) -> int:
    """Full-screen live dashboard of every agent (like k9s/htop)."""
    from z_cli.tui.top import run_top

    return run_top(state.get(), refresh_s=refresh)


def main() -> None:
    app()
