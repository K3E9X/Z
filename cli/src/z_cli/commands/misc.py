"""config, completions, usage, inference status."""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.text import Text

from z_cli import credentials, output, state
from z_cli.commands._common import JsonOpt, command, since_param
from z_cli.config import KEYS
from z_cli.errors import UsageError
from z_cli.render import tokens

config_app = typer.Typer(help="Settings and server profiles (~/.config/z/config.toml).", no_args_is_help=True)
inference_app = typer.Typer(help="Inference cluster (inferX) health.", no_args_is_help=True)
top_level = typer.Typer()

ProfileOpt = Annotated[str | None, typer.Option("--profile", help="Profile to change (default: the active one).")]


@command(config_app, "get")
def config_get(key: str, profile: ProfileOpt = None, as_json: JsonOpt = False) -> None:
    """Print one setting."""
    st = state.get()
    if key not in KEYS:
        raise UsageError(f"unknown config key {key!r}", hint="Keys: " + ", ".join(KEYS))
    value = st.config.get(key, profile or st.profile)
    if as_json:
        output.print_json({key: value})
    elif value is not None:
        output.out().print(value, markup=False)
    else:
        raise typer.Exit(1)


@command(config_app, "set")
def config_set(key: str, value: str, profile: ProfileOpt = None, as_json: JsonOpt = False) -> None:
    """Change one setting (url, team, theme, autonomy_default, editor)."""
    st = state.get()
    saved = st.config.set(key, value, profile or st.profile)
    st.config.save()
    if as_json:
        output.print_json({key: saved})
    else:
        output.success(f"{key} = {saved} ({st.config.profile_name(profile or st.profile)} profile)")


@command(config_app, "unset")
def config_unset(key: str, profile: ProfileOpt = None) -> None:
    """Remove one setting."""
    st = state.get()
    if st.config.unset(key, profile or st.profile):
        st.config.save()
        output.success(f"{key} removed")
    else:
        output.note(f"{key} was not set")


@command(config_app, "list")
def config_list(as_json: JsonOpt = False) -> None:
    """Every profile and setting (tokens are never shown)."""
    st = state.get()
    data = st.config.as_dict()
    if as_json:
        output.print_json(data)
        return
    console = output.out()
    console.print(Text(f"{data['path']}", style="z.d"))
    for name, values in data["profiles"].items():
        head = Text(name, style="z.b")
        if name == data["active"]:
            head.append("  (active)", style="z.ok")
        url = values.get("url")
        if url:
            head.append("  signed in" if credentials.load(url) else "  not signed in", style="z.d")
        console.print(head)
        for key in KEYS:
            if key in values:
                console.print(Text(f"  {key} = {values[key]}"))


@command(config_app, "profiles")
def config_profiles(as_json: JsonOpt = False) -> None:
    """List server profiles."""
    st = state.get()
    data = st.config.as_dict()
    rows = [
        {
            "name": n,
            "url": v.get("url"),
            "active": n == data["active"],
            "signed_in": bool(v.get("url") and credentials.load(v["url"])),
        }
        for n, v in data["profiles"].items()
    ]
    output.emit(
        rows,
        as_json,
        lambda r: output.table(
            ["profile", "url", "", "token"],
            [
                [x["name"], x["url"] or "", "active" if x["active"] else "", "yes" if x["signed_in"] else "no"]
                for x in r
            ],
        ),
    )


@command(config_app, "add")
def config_add(
    name: str, url: str, use: Annotated[bool, typer.Option("--use", help="Make it the active profile.")] = False
) -> None:
    """Add a server profile."""
    st = state.get()
    st.config.add_profile(name, url)
    if use:
        st.config.use(name)
    st.config.save()
    output.success(f"Profile {name} → {st.config.profiles[name]['url']}" + (" (active)" if use else ""))


@command(config_app, "use")
def config_use(name: str) -> None:
    """Switch the active profile."""
    st = state.get()
    st.config.use(name)
    st.config.save()
    output.success(f"Active profile: {name}")


@command(config_app, "remove")
def config_remove(name: str) -> None:
    """Remove a profile (its token stays until `z logout`)."""
    st = state.get()
    st.config.remove_profile(name)
    st.config.save()
    output.success(f"Profile {name} removed")


@command(config_app, "path")
def config_path() -> None:
    """Where the config file is."""
    output.out().print(str(state.get().config.path), markup=False)


# ---------------------------------------------------------------- completions

SHELLS = ("bash", "zsh", "fish", "powershell")


@command(top_level, "completions")
def completions(
    shell: Annotated[str, typer.Argument(help="bash, zsh, fish or powershell.")],
    prog: Annotated[str, typer.Option("--prog", help="Command name to complete (z or zctl).")] = "z",
) -> None:
    """Print the shell completion script: `z completions zsh > ~/.zfunc/_z` (or `eval "$(z completions bash)"`)."""
    if shell not in SHELLS:
        raise UsageError(f"shell must be one of {', '.join(SHELLS)}")
    from typer._completion_shared import get_completion_script

    var = f"_{prog.replace('-', '_').upper()}_COMPLETE"
    script = get_completion_script(prog_name=prog, complete_var=var, shell=shell)
    typer.echo(script)


# ---------------------------------------------------------------- usage and inference (B1 endpoints)


@command(top_level, "usage")
def usage(
    by: Annotated[str, typer.Option("--by", help="agent, model, mission, team or user.")] = "agent",
    since: Annotated[str, typer.Option("--since", help="7d, 24h, …")] = "7d",
    as_json: JsonOpt = False,
) -> None:
    """Token usage grouped by agent, model, mission, team or user."""
    if by not in {"agent", "model", "mission", "team", "user"}:
        raise UsageError("--by must be agent, model, mission, team or user")
    data = state.get().api().get("/usage", {"group_by": by, "from": since_param(since)}, feature="Usage (GET /usage)")
    if as_json:
        output.print_json(data)
        return
    series: list[dict[str, Any]] = data.get("series", [])
    output.out().print(
        output.table(
            [by, "tokens in", "tokens out", "cached", "requests", "cap"],
            [
                [
                    str(i.get("label") or i["key"]),
                    tokens(i["tokens_in"]),
                    tokens(i["tokens_out"]),
                    tokens(i["cached_tokens"]),
                    str(i["requests"]),
                    tokens(i["cap"]) if i.get("cap") else "",
                ]
                for i in series
            ],
        )
    )


@command(inference_app, "status")
def inference_status(as_json: JsonOpt = False) -> None:
    """inferX health, model aliases and nodes."""
    data = state.get().api().get("/inference/health", feature="Inference health (GET /inference/health)")
    output.emit(data, as_json, inference_view)


def inference_view(d: dict[str, Any]) -> Any:
    from rich.console import Group

    from z_cli.theme import state_style

    gateway = d.get("gateway") or {}
    head = Text("inferX ", style="z.b")
    head.append(f"● {d['status']}", style=state_style("healthy" if d["status"] == "healthy" else "degraded"))
    head.append(f" · gateway {'ok' if gateway.get('ok') else 'down'}", style="z.d")
    head.append(f" · {tokens(d.get('tokens_today'))} tokens today", style="z.d")
    models = output.table(
        ["alias", "provider", "status", "tok/s", "ttft p95", "running", "waiting", "tool calls"],
        [
            [
                m["alias"],
                m["provider"],
                Text(m["status"], style=state_style("healthy" if m["status"] == "healthy" else "degraded")),
                f"{m['tokens_per_s']:.0f}" if m.get("tokens_per_s") is not None else "",
                f"{m['ttft_p95_ms']:.0f}ms" if m.get("ttft_p95_ms") is not None else "",
                str(m.get("requests_running") if m.get("requests_running") is not None else ""),
                str(m.get("requests_waiting") if m.get("requests_waiting") is not None else ""),
                {True: "ok", False: "broken", None: ""}[m.get("tool_call_ok")],
            ]
            for m in d.get("models") or []
        ],
    )
    return Group(head, Text(""), models)
