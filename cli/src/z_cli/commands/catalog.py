"""Agents, skills, playbooks, connectors (catalog and exposure review)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text

from z_cli import output, state
from z_cli.commands import missions as mission_cmds
from z_cli.commands._common import JsonOpt, LimitOpt, YesOpt, command, confirm, interactive
from z_cli.errors import UsageError
from z_cli.render import ms, tokens, when
from z_cli.theme import state_style

agents_app = typer.Typer(help="Agents: who does the work.", no_args_is_help=True)
skills_app = typer.Typer(help="Skills: versioned know-how the agents load.", no_args_is_help=True)
playbooks_app = typer.Typer(help="Playbooks: reusable missions.", no_args_is_help=True)
connectors_app = typer.Typer(help="Connectors: the tools agents reach through the Z gateway.", no_args_is_help=True)


# ---------------------------------------------------------------- agents


@command(agents_app, "list")
def agents_list(
    status: Annotated[str | None, typer.Option(help="working, available or disabled.")] = None,
    query: Annotated[str | None, typer.Option("--query", "-q")] = None,
    limit: LimitOpt = 200,
    as_json: JsonOpt = False,
) -> None:
    """List agents with their status."""
    items = list(state.get().api().paginate("/agents", {"status": status, "q": query}, limit=limit))
    output.emit(
        items,
        as_json,
        lambda data: output.table(
            ["agent", "handle", "role", "model", "autonomy", "status", "team", "missions"],
            [
                [
                    Text(a["display_name"], style="z.b"),
                    f"@{a['handle']}",
                    a["role"][:50],
                    a["model"],
                    a["autonomy"],
                    Text(a["status"], style=state_style(a["status"])),
                    a.get("owner_team") or "",
                    str(len(a.get("current_missions") or [])) if a.get("current_missions") else "",
                ]
                for a in data
            ],
        ),
    )


@command(agents_app, "show")
def agents_show(agent: Annotated[str, typer.Argument(help="@handle, name or id.")], as_json: JsonOpt = False) -> None:
    """Show an agent profile."""
    a = state.get().api().get(f"/agents/{agent.removeprefix('@')}")
    if as_json:
        output.print_json(a)
        return
    limits = a.get("limits") or {}
    evals = a.get("evals_summary") or {}
    rows = output.kv(
        [
            ("agent", Text(f"{a['display_name']}  @{a['handle']}", style="z.b")),
            ("name", f"{a['name']} v{a['version']}" + (f" · pack {a['pack']}" if a.get("pack") else "")),
            ("role", a["role"]),
            ("status", Text(a["status"], style=state_style(a["status"]))),
            ("model", a["model"] + (f" (escalates to {a['escalate_model']})" if a.get("escalate_model") else "")),
            ("autonomy", a["autonomy"]),
            (
                "limits",
                f"{limits.get('max_steps')} steps · {tokens(limits.get('max_tokens'))} tokens · {limits.get('timeout_s')}s",
            ),
            ("skills", ", ".join(a.get("skills") or []) or None),
            ("tools", ", ".join(a.get("tools") or []) or None),
            ("team", a.get("owner_team")),
            ("visibility", a.get("visibility")),
            ("evals", f"{evals.get('passed')}/{evals.get('total')} passed" if evals else None),
            ("missions", ", ".join(a.get("current_missions") or []) or None),
            ("updated", when(a.get("updated_at"))),
        ]
    )
    parts: list[Any] = [rows]
    if a.get("instructions"):
        parts += [Text(""), Text("instructions", style="z.d"), Text(a["instructions"][:4000])]
    output.out().print(Group(*parts))


@command(agents_app, "versions")
def agents_versions(agent: str, as_json: JsonOpt = False) -> None:
    """Version history of an agent."""
    rows = state.get().api().get(f"/agents/{agent.removeprefix('@')}/versions")
    output.emit(
        rows,
        as_json,
        lambda data: output.table(
            ["version", "created", "by", "note"],
            [
                [v["version"], when(v["created_at"]), v.get("created_by") or "", v.get("change_note") or ""]
                for v in data
            ],
        ),
    )


# ---------------------------------------------------------------- skills


@command(skills_app, "list")
def skills_list(
    agent: Annotated[str | None, typer.Option(help="Only skills of this agent.")] = None,
    origin: Annotated[str | None, typer.Option(help="pack, learned or user.")] = None,
    limit: LimitOpt = 200,
    as_json: JsonOpt = False,
) -> None:
    """List skills."""
    items = list(
        state.get()
        .api()
        .paginate("/skills", {"agent": agent and agent.removeprefix("@"), "origin": origin}, limit=limit)
    )
    output.emit(
        items,
        as_json,
        lambda data: output.table(
            ["skill", "version", "origin", "agents", "description"],
            [
                [
                    Text(s["name"], style="z.b"),
                    s["version"],
                    s["origin"],
                    ", ".join(s.get("agents") or []),
                    s["description"][:60],
                ]
                for s in data
            ],
        ),
    )


@command(skills_app, "show")
def skills_show(skill: str, as_json: JsonOpt = False) -> None:
    """Show a skill and its SKILL.md."""
    s = state.get().api().get(f"/skills/{skill}")
    if as_json:
        output.print_json(s)
        return
    rows = output.kv(
        [
            ("skill", Text(f"{s['name']} v{s['version']}", style="z.b")),
            ("origin", s["origin"] + (f" · pack {s['pack']}" if s.get("pack") else "")),
            ("agents", ", ".join(s.get("agents") or []) or None),
            ("checksum", s["checksum"]),
            ("pending proposal", s.get("pending_proposal_id")),
        ]
    )
    parts: list[Any] = [rows, Text("")]
    if s.get("content"):
        parts.append(Markdown(s["content"]))
    output.out().print(Group(*parts))


@command(skills_app, "versions")
def skills_versions(skill: str, as_json: JsonOpt = False) -> None:
    """Version history of a skill."""
    rows = state.get().api().get(f"/skills/{skill}/versions")
    output.emit(
        rows,
        as_json,
        lambda data: output.table(
            ["version", "origin", "created", "by", "checksum"],
            [
                [v["version"], v["origin"], when(v["created_at"]), v.get("created_by") or "", v["checksum"][:16]]
                for v in data
            ],
        ),
    )


@command(skills_app, "rollback")
def skills_rollback(
    skill: str,
    to: Annotated[str, typer.Option("--to", help="The version to go back to (see `z skills versions`).")],
    reason: Annotated[str | None, typer.Option(help="Why, kept in the audit log.")] = None,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Roll a skill back to an earlier version; agents load it from their next job."""
    if not confirm(f"Roll {skill} back to {to}?", yes=yes or as_json):
        raise typer.Exit(1)
    body: dict[str, Any] = {"to_version": to}
    if reason:
        body["reason"] = reason
    s = state.get().api().post(f"/skills/{skill}/rollback", body)
    if as_json:
        output.print_json(s)
    else:
        output.success(f"{s['name']} is back to v{s['version']}")


# ---------------------------------------------------------------- playbooks


@command(playbooks_app, "list")
def playbooks_list(
    domain: Annotated[str | None, typer.Option(help="Only this domain.")] = None,
    query: Annotated[str | None, typer.Option("--query", "-q")] = None,
    limit: LimitOpt = 200,
    as_json: JsonOpt = False,
) -> None:
    """List playbooks."""
    items = list(state.get().api().paginate("/playbooks", {"domain": domain, "q": query}, limit=limit))
    output.emit(
        items,
        as_json,
        lambda data: output.table(
            ["playbook", "version", "domain", "connectors", "writes", "runs", "description"],
            [
                [
                    Text(p["name"], style="z.b"),
                    p["version"],
                    p.get("domain") or "",
                    ", ".join(p.get("connectors") or []),
                    "yes" if any(s.get("writes") for s in p.get("steps") or []) else "",
                    str(p.get("usage_count", 0)),
                    p["description"][:50],
                ]
                for p in data
            ],
        ),
    )


def playbook_inputs(p: dict[str, Any]) -> list[tuple[str, str, bool]]:
    schema = (p.get("inputs") or {}).get("schema") or {}
    required = set(schema.get("required") or [])
    out = []
    for name, prop in (schema.get("properties") or {}).items():
        kind = prop.get("type", "string")
        desc = prop.get("description", "")
        if "enum" in prop:
            desc = (desc + " " if desc else "") + "one of " + ", ".join(map(str, prop["enum"]))
        out.append((name, f"{kind}{' · ' + desc if desc else ''}", name in required))
    return out


@command(playbooks_app, "show")
def playbooks_show(playbook: str, as_json: JsonOpt = False) -> None:
    """Show a playbook: inputs, steps, writes."""
    p = state.get().api().get(f"/playbooks/{playbook}")
    if as_json:
        output.print_json(p)
        return
    budget = p.get("budget") or {}
    rows = output.kv(
        [
            ("playbook", Text(f"{p['name']} v{p['version']}", style="z.b")),
            ("description", p["description"]),
            ("domain", p.get("domain")),
            ("connectors", ", ".join(p.get("connectors") or []) or None),
            ("autonomy", p.get("autonomy")),
            ("four eyes", "yes" if p.get("four_eyes") else None),
            ("data class", p.get("data_class")),
            ("budget", f"{tokens(budget.get('max_tokens'))} tokens · {budget.get('timeout_s')}s" if budget else None),
            (
                "runs",
                f"{p.get('usage_count', 0)}" + (f" · last {when(p['last_run_at'])}" if p.get("last_run_at") else ""),
            ),
        ]
    )
    parts: list[Any] = [rows]
    inputs = playbook_inputs(p)
    if inputs:
        parts += [Text(""), Text("inputs", style="z.d")]
        for name, desc, req in inputs:
            t = Text(f"  {name}", style="z.b")
            t.append(" (required)" if req else "", style="z.wait")
            t.append(f"  {desc}", style="z.d")
            parts.append(t)
    parts += [Text(""), Text("steps", style="z.d")]
    for step in p.get("steps") or []:
        t = Text(f"  {step['id']}", style="z.b")
        t.append(f"  @{step['agent']}", style="z.d")
        if step.get("fanout"):
            t.append(f"  fan-out over {step['fanout']['over']}", style="z.d")
        if step.get("writes"):
            t.append("  writes " + ", ".join(step["writes"]), style="z.wait")
        parts.append(t)
        if step.get("title"):
            parts.append(Text(f"    {step['title']}", style="z.m"))
    parts += [
        Text(""),
        Text(f"Run it: z run {p['name']}" + "".join(f" --input {n}=…" for n, _, r in inputs if r), style="z.d"),
    ]
    output.out().print(Group(*parts))


@command(playbooks_app, "run")
def playbooks_run(
    playbook: str,
    input_: Annotated[list[str] | None, typer.Option("--input", "-i", help="key=value or key:=<json>.")] = None,
    file: Annotated[list[Path] | None, typer.Option("--file", "-f", help="Workspace input file (repeat).")] = None,
    plan_first: Annotated[bool, typer.Option("--plan-first")] = False,
    wait: Annotated[bool, typer.Option("--wait", "-w")] = False,
    output_format: mission_cmds.OutputFormat = "text",
    as_json: JsonOpt = False,
) -> int:
    """Run a playbook (same as `z run`); asks for missing required inputs in a terminal."""
    api = state.get().api()
    pairs = list(input_ or [])
    if interactive():
        p = api.get(f"/playbooks/{playbook}")
        given = {pair.split("=", 1)[0].rstrip(":") for pair in pairs if "=" in pair}
        from z_cli.commands._common import ask

        for name, desc, required in playbook_inputs(p):
            if required and name not in given:
                pairs.append(f"{name}={ask(f'{name} ({desc})')}")
    mission = mission_cmds.run_playbook(api, playbook, pairs, file or [], plan_first=plan_first)
    if as_json and output_format == "text":
        output_format = "json"
    if not wait:
        if output_format in {"json", "stream-json"}:
            output.print_json(mission)
        else:
            output.success(f"Mission {mission['id']} {mission['status']} · {mission['title'][:70]}")
        return 0
    return mission_cmds.follow_to_end(api, mission, output_format, decide=True, stop_on_attention=not interactive())


# ---------------------------------------------------------------- connectors

HEALTH_MARK = {
    "healthy": ("●", "z.ok"),
    "degraded": ("▲", "z.wait"),
    "token_expiring": ("▲", "z.wait"),
    "unreachable": ("✕", "z.err"),
    "disabled": ("○", "z.d"),
}


def health_text(health: dict[str, Any]) -> Text:
    status = health.get("status", "?")
    glyph, style = HEALTH_MARK.get(status, ("?", "z.d"))
    t = Text(f"{glyph} {status}", style=style)
    if health.get("latency_p95_ms") is not None:
        t.append(f" · p95 {ms(health['latency_p95_ms'])}", style="z.d")
    return t


@command(connectors_app, "list")
def connectors_list(as_json: JsonOpt = False) -> None:
    """List connectors with health, latency and exposure review status."""
    items = state.get().api().get("/connectors")
    output.emit(
        items,
        as_json,
        lambda data: output.table(
            ["connector", "kind", "health", "class", "review", "exposed", "used by"],
            [
                [
                    Text(c["name"], style="z.b"),
                    c["kind"],
                    health_text(c["health"]),
                    c["data_class"],
                    Text(c["review_status"], style="z.wait" if c["review_status"] == "pending_review" else "z.ok"),
                    f"{c['operations_exposed']}/{c['operations_total']}",
                    ", ".join(c.get("used_by") or [])[:40],
                ]
                for c in data
            ],
        ),
    )


@command(connectors_app, "show")
def connectors_show(connector: str, as_json: JsonOpt = False) -> None:
    """Show a connector: health, credentials (references only), operations summary."""
    c = state.get().api().get(f"/connectors/{connector}")
    if as_json:
        output.print_json(c)
        return
    health = c.get("health") or {}
    rows = output.kv(
        [
            ("connector", Text(f"{c['display_name']}  ({c['name']}, {c['kind']})", style="z.b")),
            ("base url", c.get("base_url")),
            ("health", health_text(health)),
            ("last success", when(health.get("last_success_at")) or None),
            ("detail", health.get("detail")),
            ("data class", c["data_class"]),
            ("review", Text(c["review_status"], style="z.wait" if c["review_status"] == "pending_review" else "z.ok")),
            ("enabled", "yes" if c["enabled"] else "no"),
            ("operations", f"{c['operations_exposed']} exposed of {c['operations_total']}"),
            ("used by", ", ".join(c.get("used_by") or []) or None),
            ("pack", c.get("pack")),
        ]
    )
    parts: list[Any] = [rows]
    if c.get("credentials"):
        parts += [
            Text(""),
            output.table(
                ["credential", "purpose", "scope", "backend", "identity", "expires"],
                [
                    [
                        cr["id"],
                        cr["purpose"],
                        cr["scope"],
                        f"{cr['backend']}:{cr['ref']}",
                        cr.get("identity") or "",
                        when(cr.get("expires_at")),
                    ]
                    for cr in c["credentials"]
                ],
            ),
        ]
    output.out().print(Group(*parts))


@command(connectors_app, "operations")
def connectors_operations(
    connector: str,
    exposed: Annotated[bool | None, typer.Option("--exposed/--not-exposed", help="Filter on exposure.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """List a connector's operations with their class and exposure."""
    ops = state.get().api().get(f"/connectors/{connector}/operations")
    if exposed is not None:
        ops = [o for o in ops if bool(o["exposed"]) == exposed]
    output.emit(
        ops,
        as_json,
        lambda data: output.table(
            ["operation", "class", "classifier", "exposed", "proposed", "reviewed", "description"],
            [
                [
                    o["id"],
                    Text(
                        o["class"],
                        style={"read": "z.ok", "write": "z.wait", "destructive": "z.err"}.get(o["class"], ""),
                    ),
                    o["classifier_class"],
                    "yes" if o["exposed"] else "no",
                    "yes" if o["proposed_exposed"] else "no",
                    o.get("reviewed_by") or "",
                    (o.get("description") or "")[:50],
                ]
                for o in data
            ],
        ),
    )


@command(connectors_app, "approve-exposure")
def connectors_approve_exposure(
    connector: str,
    expose: Annotated[list[str] | None, typer.Option("--expose", help="Operation to expose (repeat).")] = None,
    hide: Annotated[list[str] | None, typer.Option("--hide", help="Operation to keep hidden (repeat).")] = None,
    as_proposed: Annotated[
        bool, typer.Option("--as-proposed", help="Accept the pack's proposed exposure for the rest.")
    ] = False,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Confirm which operations agents may see (pack exposure stays pending_review until an admin confirms)."""
    api = state.get().api()
    ops = api.get(f"/connectors/{connector}/operations")
    ids = {o["id"] for o in ops}
    unknown = sorted((set(expose or []) | set(hide or [])) - ids)
    if unknown:
        raise UsageError(f"unknown operations: {', '.join(unknown)}")
    decisions = []
    for o in ops:
        if o["id"] in (expose or []):
            value = True
        elif o["id"] in (hide or []):
            value = False
        elif as_proposed:
            value = bool(o["proposed_exposed"])
        else:
            value = bool(o["exposed"])
        decisions.append({"id": o["id"], "exposed": value})
    changes = [
        d for d, o in zip(decisions, ops, strict=True) if d["exposed"] != o["exposed"] or o.get("reviewed_by") is None
    ]
    if not as_json:
        output.err().print(
            output.table(
                ["operation", "class", "now", "after"],
                [
                    [
                        o["id"],
                        o["class"],
                        "exposed" if o["exposed"] else "hidden",
                        Text(
                            "exposed" if d["exposed"] else "hidden",
                            style="z.wait" if d["exposed"] and o["class"] != "read" else "",
                        ),
                    ]
                    for d, o in zip(decisions, ops, strict=True)
                ],
            )
        )
        destructive = [
            d["id"] for d, o in zip(decisions, ops, strict=True) if d["exposed"] and o["class"] == "destructive"
        ]
        if destructive:
            output.note(
                "Destructive operations exposed: " + ", ".join(destructive) + " (two approvers per action)", "z.err"
            )
    if not confirm(f"Save this exposure for {connector} ({len(changes)} decisions)?", yes=yes or as_json):
        raise typer.Exit(1)
    result = api.post(f"/connectors/{connector}/approve-exposure", {"operations": decisions})
    if as_json:
        output.print_json(result)
    else:
        output.success(
            f"{connector}: exposure reviewed ({result['operations_exposed']}/{result['operations_total']} exposed)"
        )


@command(connectors_app, "set-operation")
def connectors_set_operation(
    operation: Annotated[str, typer.Argument(help="Full operation id, e.g. jira.issues.create.")],
    class_: Annotated[
        str | None, typer.Option("--class", help="read, write or destructive (rules can only raise it).")
    ] = None,
    exposed: Annotated[bool | None, typer.Option("--expose/--hide")] = None,
    rate_limit: Annotated[
        str | None, typer.Option("--rate-limit", help="JSON object, e.g. '{\"per_minute\": 30}'.")
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Change one operation's class, exposure or rate limit."""
    body: dict[str, Any] = {}
    if class_:
        if class_ not in {"read", "write", "destructive"}:
            raise UsageError("--class must be read, write or destructive")
        body["class"] = class_
    if exposed is not None:
        body["exposed"] = exposed
    if rate_limit:
        try:
            body["rate_limit"] = json.loads(rate_limit)
        except ValueError as exc:
            raise UsageError(f"--rate-limit: not valid JSON ({exc})") from exc
    if not body:
        raise UsageError("nothing to change", hint="Pass --class, --expose/--hide or --rate-limit.")
    op = state.get().api().patch(f"/operations/{operation}", body)
    if as_json:
        output.print_json(op)
    else:
        output.success(f"{op['id']}: class {op['class']}, {'exposed' if op['exposed'] else 'hidden'}")


@command(connectors_app, "test")
def connectors_test(connector: str, as_json: JsonOpt = False) -> None:
    """Check a connector (health and latency) now."""
    result = (
        state.get()
        .api()
        .post(f"/connectors/{connector}/test", {}, feature="Connector tests (POST /connectors/{id}/test)")
    )
    output.emit(result, as_json, lambda r: Text(json.dumps(r, indent=2, default=str)))


@command(agents_app, "create")
def agents_create(
    describe: Annotated[
        str | None, typer.Option("--describe", help="Plain-language description: Z drafts the profile.")
    ] = None,
    from_template: Annotated[
        str | None, typer.Option("--from-template", help="Duplicate this agent as a start.")
    ] = None,
    name: Annotated[str | None, typer.Option("--name", help="Display name (e.g. Vega).")] = None,
    handle: Annotated[str | None, typer.Option("--handle", help="Handle (default: from the name).")] = None,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Create an agent from a description (agent builder) or from a template."""
    from z_cli.commands.learn import draft_agent

    api = state.get().api()
    if bool(describe) == bool(from_template):
        raise UsageError("give --describe or --from-template")
    if from_template:
        if not name:
            raise UsageError("--from-template needs --name")
        body = {"display_name": name, "handle": (handle or name).lower().replace(" ", "-")}
        created = api.post(f"/agents/{from_template.removeprefix('@')}/duplicate", body)
    else:
        drafted = draft_agent(str(describe), None)
        profile = drafted["profile"]
        if name:
            profile["display_name"] = name
        if handle:
            profile["handle"] = handle
        if not as_json:
            output.out().print(
                output.kv(
                    [
                        ("name", f"{profile['display_name']} (@{profile['handle']})"),
                        ("role", profile["role"]),
                        ("model", profile["model"]),
                        ("skills", ", ".join(profile["skills"]) or "none"),
                        ("tools", ", ".join(profile["tools"]) or "none"),
                        ("autonomy", profile["autonomy"]),
                        ("new skills", ", ".join(s["name"] for s in drafted.get("new_skills") or []) or None),
                    ]
                )
            )
            for question in drafted.get("questions") or []:
                output.note(f"Open question: {question}")
        if not confirm("Create this agent?", yes=yes, default=True):
            output.note("Nothing created.")
            return
        created = api.post("/agents", profile)
    if as_json:
        output.print_json(created)
        return
    output.success(f"Created {created['display_name']} (@{created['handle']}).")


@command(agents_app, "rename")
def agents_rename(
    agent: Annotated[str, typer.Argument(help="@handle, name or id.")],
    name: Annotated[str, typer.Option("--name", help="New display name.")],
    handle: Annotated[str | None, typer.Option("--handle", help="New handle.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Rename an agent (display name and handle; history is kept)."""
    body = {"display_name": name, "handle": handle or name.lower().replace(" ", "-")}
    updated = state.get().api().patch(f"/agents/{agent.removeprefix('@')}", body)
    if as_json:
        output.print_json(updated)
        return
    output.success(f"Renamed to {updated['display_name']} (@{updated['handle']}).")


@command(agents_app, "duplicate")
def agents_duplicate(
    agent: Annotated[str, typer.Argument(help="@handle, name or id.")],
    name: Annotated[str, typer.Option("--name", help="Display name of the copy.")],
    handle: Annotated[str | None, typer.Option("--handle", help="Handle of the copy.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Duplicate an agent under a new name."""
    body = {"display_name": name, "handle": handle or name.lower().replace(" ", "-")}
    created = state.get().api().post(f"/agents/{agent.removeprefix('@')}/duplicate", body)
    if as_json:
        output.print_json(created)
        return
    output.success(f"Created {created['display_name']} (@{created['handle']}).")
