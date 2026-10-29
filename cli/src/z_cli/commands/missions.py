"""Missions: list, show, start, pause, resume, cancel, guide, watch, replay; `z run`, `z ask`, `z logs`, headless."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Group
from rich.text import Text

from z_cli import contract, output, review, sse, state
from z_cli import missions as ms
from z_cli.client import Api
from z_cli.commands._common import JsonOpt, LimitOpt, YesOpt, command, confirm, exit_code_for_mission, interactive
from z_cli.errors import CliError, ExitCode, UsageError
from z_cli.live import TERMINAL_MISSION, MissionView
from z_cli.render import event_line, mission_tree, status_text, tokens, when
from z_cli.theme import state_style

app = typer.Typer(help="Missions: list, inspect, control and follow them.", no_args_is_help=True)
top_level = typer.Typer()

OutputFormat = Annotated[
    str,
    typer.Option(
        "--output-format",
        help="text (live tree on a terminal, one line per event otherwise), json (final mission), stream-json "
        "(one JSON event per line, then the final mission).",
    ),
]
MENTION = re.compile(r"(?<![\w@#])([@#])([a-z0-9][a-z0-9_.-]{0,62})", re.IGNORECASE)


def mentions(text: str) -> tuple[list[str], list[str]]:
    agents: list[str] = []
    connectors: list[str] = []
    for sign, name in MENTION.findall(text):
        name = name.rstrip(".").lower()
        (agents if sign == "@" else connectors).append(name)
    return list(dict.fromkeys(agents)), list(dict.fromkeys(connectors))


# ---------------------------------------------------------------- rendering


def mission_rows(items: list[dict[str, Any]]) -> Any:
    return output.table(
        ["id", "title", ("status", {"no_wrap": True}), "playbook", "by", "tokens", "decisions", "created"],
        [
            [
                Text(m["id"], style="z.d"),
                m["title"][:60],
                status_text(m["status"], m.get("waiting_reason")),
                m.get("playbook") or "",
                m.get("created_by", ""),
                tokens(int(m.get("tokens_in", 0)) + int(m.get("tokens_out", 0))),
                str((m.get("pending_actions") or 0) + (m.get("pending_questions") or 0) or ""),
                when(m.get("created_at")),
            ]
            for m in items
        ],
    )


def mission_detail(m: dict[str, Any], jobs: list[dict[str, Any]] | None = None) -> Group:
    view = MissionView(m["id"])
    view.load_mission(m)
    for job in jobs or []:
        view.load_job(job)
    counts = m.get("jobs") or {}
    rows = output.kv(
        [
            ("title", Text(m["title"], style="z.b")),
            ("status", status_text(m["status"], m.get("waiting_reason"))),
            ("playbook", f"{m['playbook']} v{m.get('playbook_version')}" if m.get("playbook") else None),
            ("prompt", (m.get("prompt") or "")[:400] or None),
            ("inputs", ", ".join(f"{k}={v}" for k, v in (m.get("inputs") or {}).items()) or None),
            ("autonomy", str(m.get("autonomy")) + (" · plan first" if m.get("plan_first") else "")),
            ("data class", m.get("data_class")),
            ("connectors", ", ".join(m.get("connectors") or []) or None),
            (
                "created",
                f"{when(m.get('created_at'))} by {m.get('created_by')}"
                + (f" · team {m['team']}" if m.get("team") else ""),
            ),
            ("started", when(m.get("started_at")) or None),
            ("ended", when(m.get("ended_at")) or None),
            (
                "budget",
                f"{tokens(m['budget'].get('max_tokens'))} tokens · {m['budget'].get('timeout_s')}s"
                if m.get("budget")
                else None,
            ),
            ("tokens", f"{tokens(m.get('tokens_in'))} in · {tokens(m.get('tokens_out'))} out"),
            (
                "jobs",
                " · ".join(f"{v} {k}" for k, v in counts.items() if v and k != "total") or None,
            ),
            (
                "decisions",
                f"{m.get('pending_actions', 0)} writes, {m.get('pending_questions', 0)} questions waiting"
                if (m.get("pending_actions") or m.get("pending_questions"))
                else None,
            ),
            ("error", Text(m["error"], style="z.err") if m.get("error") else None),
        ]
    )
    parts: list[Any] = [rows]
    if view.steps:
        parts += [Text(""), mission_tree(view, show_jobs=False)]
    if jobs:
        parts += [
            Text(""),
            output.table(
                ["job", "step", "agent", "status", "attempt", "steps", "tokens"],
                [
                    [
                        Text(j["id"], style="z.d"),
                        j["step_id"] + (f" [{j['item_key']}]" if j.get("item_key") else ""),
                        f"@{j['agent']}",
                        Text(
                            j["status"]
                            + (f" ({j['needs_attention_reason']})" if j.get("needs_attention_reason") else ""),
                            style=state_style(j["status"]),
                        ),
                        str(j["attempt"]),
                        str(j["steps_done"]),
                        tokens(int(j["tokens_in"]) + int(j["tokens_out"])),
                    ]
                    for j in jobs
                ],
            ),
        ]
    return Group(*parts)


# ---------------------------------------------------------------- list / show


@command(app, "list")
def list_missions(
    status: Annotated[
        str | None, typer.Option("--status", "-s", help="Comma-separated statuses, e.g. running,waiting.")
    ] = None,
    playbook: Annotated[str | None, typer.Option(help="Only missions of this playbook.")] = None,
    agent: Annotated[str | None, typer.Option(help="Only missions with a job of this agent (@handle).")] = None,
    mine: Annotated[bool, typer.Option("--mine", help="Only missions you started.")] = False,
    query: Annotated[str | None, typer.Option("--query", "-q", help="Search title and prompt.")] = None,
    limit: LimitOpt = 50,
    as_json: JsonOpt = False,
) -> None:
    """List missions (newest first)."""
    st = state.get()
    api = st.api()
    created_by = None
    if mine:
        created_by = api.get("/me")["user"]["username"]
    items = list(
        api.paginate(
            "/missions",
            {"status": status, "playbook": playbook, "agent": agent, "created_by": created_by, "q": query},
            limit=limit,
        )
    )
    if as_json:
        output.print_json(items)
        return
    if not items:
        output.note("No missions match.")
        return
    output.out().print(mission_rows(items))


@command(app, "show")
def show(mission_id: str, as_json: JsonOpt = False) -> None:
    """Show a mission: status, plan, jobs, tokens, decisions."""
    api = state.get().api()
    mission = api.get(f"/missions/{mission_id}")
    jobs = list(api.paginate(f"/missions/{mission_id}/jobs", limit=500))
    if as_json:
        output.print_json(mission)
        return
    output.out().print(mission_detail(mission, jobs))


def _control(verb: str, mission_id: str, reason: str | None, as_json: bool) -> None:
    mission = state.get().api().post(f"/missions/{mission_id}/{verb}", {"reason": reason} if reason else {})
    if as_json:
        output.print_json(mission)
        return
    line = Text()
    line.append("● ", style=state_style(mission["status"]))
    line.append(f"{mission['id']} ")
    line.append(mission["status"], style=state_style(mission["status"]))
    line.append(f" · {mission['title'][:70]}", style="z.d")
    output.out().print(line)


ReasonOpt = Annotated[str | None, typer.Option("--reason", "-r", help="Why (kept in the audit log).")]


@command(app, "start")
def start(mission_id: str, reason: ReasonOpt = None, as_json: JsonOpt = False) -> None:
    """Start a draft or planned mission (e.g. after reviewing a plan-first plan)."""
    _control("start", mission_id, reason, as_json)


@command(app, "pause")
def pause(mission_id: str, reason: ReasonOpt = None, as_json: JsonOpt = False) -> None:
    """Pause a running mission (its agents checkpoint and wait)."""
    _control("pause", mission_id, reason, as_json)


@command(app, "resume")
def resume(mission_id: str, reason: ReasonOpt = None, as_json: JsonOpt = False) -> None:
    """Resume a paused mission."""
    _control("resume", mission_id, reason, as_json)


@command(app, "cancel")
def cancel(mission_id: str, reason: ReasonOpt = None, yes: YesOpt = False, as_json: JsonOpt = False) -> None:
    """Stop a mission for good (its agents are stopped; pending writes are not applied)."""
    if not confirm(f"Cancel mission {mission_id}?", yes=yes or as_json):
        raise typer.Exit(1)
    _control("cancel", mission_id, reason, as_json)


@command(app, "stop")
def stop(mission_id: str, reason: ReasonOpt = None, yes: YesOpt = False, as_json: JsonOpt = False) -> None:
    """Alias of cancel."""
    if not confirm(f"Stop mission {mission_id}?", yes=yes or as_json):
        raise typer.Exit(1)
    _control("cancel", mission_id, reason, as_json)


@command(app, "guide")
def guide(
    mission_id: str,
    text: Annotated[str, typer.Argument(help="What to tell the running agents.")],
    job: Annotated[str | None, typer.Option("--job", help="Only this job.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Send guidance to the running agents of a mission."""
    body: dict[str, Any] = {"text": text}
    if job:
        body["job_id"] = job
    state.get().api().post(f"/missions/{mission_id}/messages", body)
    if as_json:
        output.print_json({"mission_id": mission_id, "delivered": True})
    else:
        output.success("Guidance sent; the agents read it at their next step.")


# ---------------------------------------------------------------- watch / logs / replay


def follow_to_end(
    api: Api, mission: dict[str, Any], output_format: str, *, decide: bool, stop_on_attention: bool
) -> int:
    """Follow a mission; returns the exit code of docs/10 §3."""
    mode = ms.choose_mode(output_format)
    follower = ms.Follower(api, mission, mode)
    stop = ms.needs_attention if (decide or stop_on_attention) else (lambda view: False)
    while True:
        final = follower.run(stop=stop)
        if str(final["status"]) in TERMINAL_MISSION or not ms.needs_attention(follower.view):
            break
        if not (decide and interactive()):
            break  # headless: stop at the first decision (exit 3)
        _decide_inline(api, final)
    final = follower.refresh()
    _finish(final, output_format, follower)
    return int(exit_code_for_mission(str(final["status"])))


def _decide_inline(api: Api, mission: dict[str, Any]) -> None:
    if mission["status"] == "planned":
        output.err().print(Text("The plan is ready and waits for you (plan first).", style="z.wait"))
        if confirm("Start the mission with this plan?", default=True):
            api.post(f"/missions/{mission['id']}/start", {})
        else:
            raise CliError("The mission waits for its plan to be accepted.", exit_code=ExitCode.ATTENTION)
        return
    counts = review.one_by_one(api, mission=mission["id"])
    if not any(counts[k] for k in ("approved", "rejected", "answered")):
        raise CliError(
            "The mission still waits for a decision.",
            exit_code=ExitCode.ATTENTION,
            hint=f"Decide with `z review --mission {mission['id']}` or in the web app ({api.url}).",
        )


def _finish(final: dict[str, Any], output_format: str, follower: ms.Follower) -> None:
    status = str(final["status"])
    if output_format == "json":
        output.print_json(final)
    elif output_format == "stream-json":
        output.print_jsonl({"type": "result", "mission": final})
    elif follower.mode == "lines":
        output.out().print(
            Text(
                f"mission {final['id']} {status} · {tokens(int(final['tokens_in']) + int(final['tokens_out']))} tokens",
                style=state_style(status),
            )
        )
    if status in ms.ATTENTION:
        url = follower.api.url
        what = "accept its plan" if status == "planned" else f"a decision ({final.get('waiting_reason') or 'review'})"
        output.note(f"Mission {final['id']} needs {what}: `z review --mission {final['id']}` or {url}/review", "z.wait")


@command(app, "watch")
def watch(
    mission_id: str,
    output_format: OutputFormat = "text",
    decide: Annotated[bool, typer.Option("--decide/--no-decide", help="Offer pending decisions inline.")] = True,
) -> int:
    """Follow a mission live until it ends (reconnects and resumes after network drops)."""
    api = state.get().api()
    mission = api.get(f"/missions/{mission_id}")
    return follow_to_end(api, mission, output_format, decide=decide, stop_on_attention=not interactive())


@command(app, "replay")
def replay(mission_id: str, as_json: JsonOpt = False) -> None:
    """Every event of a mission, in order, with its artifacts."""
    data = state.get().api().get(f"/missions/{mission_id}/replay")
    if as_json:
        output.print_json(data)
        return
    view = MissionView(mission_id)
    view.load_mission(data["mission"])
    console = output.out()
    for event in data["events"]:
        view.apply(event)
        console.print(event_line(event))
    console.print(Text(""))
    console.print(mission_tree(view))
    if data.get("artifacts"):
        console.print(Text(""))
        console.print(
            output.table(
                ["artifact", "kind", "name", "size", "class"],
                [[a["id"], a["kind"], a["name"], str(a["size"]), a["data_class"]] for a in data["artifacts"]],
            )
        )


@command(top_level, "logs")
def logs(
    target: Annotated[str, typer.Argument(help="Mission id (msn_…) or job id (job_…).")],
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Keep streaming new events.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="One JSON event per line.")] = False,
) -> None:
    """Activity of a mission or one job, one line per event (`-f` to stream)."""
    api = state.get().api()
    is_job = target.startswith("job")
    if is_job:
        job = api.get(f"/jobs/{target}")
        mission_id, path = job["mission_id"], f"/jobs/{target}/events"
    else:
        mission_id, path = target, f"/missions/{target}/events"
    if not follow:
        data = api.get(f"/missions/{mission_id}/replay")
        for event in data["events"]:
            if is_job and event.get("job_id") != target:
                continue
            output.print_jsonl(event) if as_json else output.out().print(event_line(event))
        return

    def finished() -> bool:
        return str(api.get(f"/missions/{mission_id}")["status"]) in TERMINAL_MISSION

    for message in sse.stream(api, path, finished=finished):
        event = contract.check_event(message.json())
        output.print_jsonl(event) if as_json else output.out().print(event_line(event))


# ---------------------------------------------------------------- run / ask / headless


def _playbook(api: Api, name: str) -> dict[str, Any]:
    try:
        playbook: dict[str, Any] = api.get(f"/playbooks/{name}")
    except CliError as exc:
        if getattr(exc, "status", None) == 404:
            raise UsageError(f"No playbook named {name!r}.", hint="List them with `z playbooks list`.") from exc
        raise
    return playbook


def run_playbook(
    api: Api,
    name: str,
    inputs: list[str],
    files: list[Path],
    *,
    title: str | None = None,
    plan_first: bool = False,
    data_class: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    playbook = _playbook(api, name)
    values = ms.parse_inputs(inputs, ((playbook.get("inputs") or {}).get("schema")))
    body: dict[str, Any] = {"playbook": playbook["name"], "inputs": values}
    if title:
        body["title"] = title
    if plan_first:
        body["plan_first"] = True
    if data_class:
        body["data_class"] = data_class
    if max_tokens:
        body["budget"] = {"max_tokens": max_tokens}
    if not files:
        run = {k: v for k, v in body.items() if k != "playbook"}
        mission: dict[str, Any] = api.post(f"/playbooks/{playbook['name']}/run", run)
        return mission
    mission = api.post("/missions", {**body, "start": False})
    ms.upload_files(api, mission["id"], files)
    if plan_first:
        return mission
    started: dict[str, Any] = api.post(f"/missions/{mission['id']}/start", {})
    return started


@command(top_level, "run")
def run(
    playbook: Annotated[str, typer.Argument(help="Playbook name, e.g. weekly-vuln-triage.")],
    input_: Annotated[
        list[str] | None,
        typer.Option("--input", "-i", help="key=value (typed from the playbook schema) or key:=<json>."),
    ] = None,
    file: Annotated[list[Path] | None, typer.Option("--file", "-f", help="Workspace input file (repeat).")] = None,
    title: Annotated[str | None, typer.Option(help="Mission title.")] = None,
    plan_first: Annotated[bool, typer.Option("--plan-first", help="Draft the plan and wait before running.")] = False,
    data_class: Annotated[str | None, typer.Option("--data-class", help="Raise the data class (C0-C3).")] = None,
    max_tokens: Annotated[int | None, typer.Option("--max-tokens", help="Token budget for this mission.")] = None,
    wait: Annotated[bool, typer.Option("--wait", "-w", help="Follow the mission until it ends.")] = False,
    output_format: OutputFormat = "text",
    as_json: JsonOpt = False,
) -> int:
    """Start a playbook. With --wait the exit code reflects the mission result."""
    api = state.get().api()
    mission = run_playbook(
        api,
        playbook,
        input_ or [],
        file or [],
        title=title,
        plan_first=plan_first,
        data_class=data_class,
        max_tokens=max_tokens,
    )
    if as_json and output_format == "text":
        output_format = "json"
    if not wait:
        if output_format in {"json", "stream-json"}:
            output.print_json(mission)
        else:
            output.success(f"Mission {mission['id']} {mission['status']} · {mission['title'][:70]}")
            output.note(f"Follow it with `z missions watch {mission['id']}`.")
        return 0
    return follow_to_end(api, mission, output_format, decide=True, stop_on_attention=not interactive())


def resolve_agents(api: Api, handles: list[str]) -> list[str]:
    """Check that every @handle exists; return them (the API takes handles)."""
    if not handles:
        return []
    known = {a["handle"]: a for a in api.paginate("/agents", limit=1000)}
    known |= {a["name"]: a for a in known.values()}
    missing = [h for h in handles if h not in known]
    if missing:
        raise UsageError(
            f"Unknown agent {', '.join('@' + m for m in missing)}.", hint="List them with `z agents list`."
        )
    return [known[h]["handle"] for h in handles]


def start_prompt(
    api: Api,
    prompt: str,
    *,
    agents: list[str],
    connectors: list[str],
    playbook: str | None = None,
    inputs: list[str] | None = None,
    autonomy: str | None = None,
    plan_first: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    if playbook:
        return run_playbook(api, playbook, inputs or [], [], title=prompt[:200] or None, plan_first=plan_first)
    found_agents, found_connectors = mentions(prompt)
    handles = resolve_agents(api, list(dict.fromkeys([*agents, *found_agents])))
    if not handles:
        raise UsageError(
            "Say which agent should do it: mention one with @handle, or pass --agent / --playbook.",
            hint="Free-form planning across agents (POST /missions/draft-plan) is not available on this server yet.",
        )
    body: dict[str, Any] = {"prompt": prompt, "agents": handles}
    conns = list(dict.fromkeys([*connectors, *found_connectors]))
    if conns:
        body["connectors"] = conns
    if autonomy:
        body["autonomy"] = autonomy
    if plan_first:
        body["plan_first"] = True
    if session_id:
        body["session_id"] = session_id
    mission: dict[str, Any] = api.post("/missions", body)
    return mission


@command(top_level, "ask")
def ask_cmd(
    prompt: Annotated[str, typer.Argument(help='What to do, e.g. "summarize SEC-12 @ticket-manager #jira".')],
    agent: Annotated[list[str] | None, typer.Argument(help="@agent handles (also read from the prompt).")] = None,
    connector: Annotated[
        list[str] | None, typer.Option("--connector", "-c", help="Connector the agent may use.")
    ] = None,
    read_only: Annotated[bool, typer.Option("--read-only", help="No write proposals at all.")] = False,
    plan_first: Annotated[bool, typer.Option("--plan-first", help="Draft the plan and wait.")] = False,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Follow the mission until it ends.")] = True,
    output_format: OutputFormat = "text",
) -> int:
    """Give one agent a free-form task (`z ask "…" @agent`)."""
    api = state.get().api()
    handles = [a.removeprefix("@") for a in agent or []]
    mission = start_prompt(
        api,
        prompt,
        agents=handles,
        connectors=[c.removeprefix("#") for c in connector or []],
        autonomy="read_only" if read_only else state.get().setting("autonomy_default"),
        plan_first=plan_first,
    )
    if not wait:
        if output_format in {"json", "stream-json"}:
            output.print_json(mission)
        else:
            output.success(f"Mission {mission['id']} {mission['status']}")
        return 0
    return follow_to_end(api, mission, output_format, decide=True, stop_on_attention=not interactive())


def headless(
    prompt: str,
    *,
    playbook: str | None,
    agents: list[str],
    inputs: list[str],
    output_format: str,
    plan_first: bool,
) -> int:
    """`z -p "…"`: start, follow, stop at the first decision (exit 3), exit with the mission result."""
    if output_format not in {"text", "json", "stream-json"}:
        raise UsageError("--output-format must be text, json or stream-json")
    api = state.get().api()
    mission = start_prompt(
        api,
        prompt,
        agents=[a.removeprefix("@") for a in agents],
        connectors=[],
        playbook=playbook,
        inputs=inputs,
        autonomy=state.get().setting("autonomy_default"),
        plan_first=plan_first,
    )
    follower = ms.Follower(api, mission, ms.choose_mode(output_format))
    final = follower.run(stop=ms.needs_attention)
    _finish(final, output_format, follower)
    return int(exit_code_for_mission(str(final["status"])))


# ---------------------------------------------------------------- top-level aliases (docs/10 §3)


@command(top_level, "pause")
def pause_alias(mission_id: str, reason: ReasonOpt = None, as_json: JsonOpt = False) -> None:
    """Pause a mission (alias of `z missions pause`)."""
    _control("pause", mission_id, reason, as_json)


@command(top_level, "resume")
def resume_alias(mission_id: str, reason: ReasonOpt = None, as_json: JsonOpt = False) -> None:
    """Resume a mission (alias of `z missions resume`)."""
    _control("resume", mission_id, reason, as_json)


@command(top_level, "stop")
def stop_alias(mission_id: str, reason: ReasonOpt = None, yes: YesOpt = False, as_json: JsonOpt = False) -> None:
    """Cancel a mission (alias of `z missions cancel`)."""
    if not confirm(f"Stop mission {mission_id}?", yes=yes or as_json):
        raise typer.Exit(1)
    _control("cancel", mission_id, reason, as_json)
