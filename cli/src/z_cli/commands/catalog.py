"""Agents, skills, playbooks, connectors (catalog and exposure review)."""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text

from z_cli import output, state
from z_cli.commands import missions as mission_cmds
from z_cli.commands._common import JsonOpt, LimitOpt, YesOpt, command, confirm, interactive
from z_cli.errors import ApiError, UsageError
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


def _version_meta(v: dict[str, Any]) -> dict[str, Any]:
    return {k: val for k, val in v.items() if k != "content"}


def diff_text(skill: str, old: dict[str, Any], new: dict[str, Any]) -> str:
    """Unified diff of two versions' SKILL.md ("" when they are the same)."""
    lines = difflib.unified_diff(
        old["content"].splitlines(keepends=True),
        new["content"].splitlines(keepends=True),
        fromfile=f"{skill}@{old['version']}/SKILL.md",
        tofile=f"{skill}@{new['version']}/SKILL.md",
    )
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def diff_view(text: str) -> Group:
    """A unified diff in the theme's colours: additions ok, removals err, hunks accent, file headers bold."""
    rows: list[Any] = []
    for line in text.splitlines():
        if line.startswith(("+++", "---")):
            style = "z.b"
        elif line.startswith("@@"):
            style = "z.acc"
        elif line.startswith("+"):
            style = "z.ok"
        elif line.startswith("-"):
            style = "z.err"
        else:
            style = "z.m"
        rows.append(Text(line, style=style))
    return Group(*rows)


@command(skills_app, "diff")
def skills_diff(
    skill: str,
    from_: Annotated[str | None, typer.Option("--from", help="Older version (default: the one before --to).")] = None,
    to: Annotated[str | None, typer.Option("--to", help="Newer version (default: the current version).")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Unified diff of two versions' SKILL.md (default: the previous version against the current one)."""
    api = state.get().api()
    current = api.get(f"/skills/{skill}")
    name = current["name"]
    target = to or current["version"]
    if from_ is None:
        history = [v["version"] for v in api.get(f"/skills/{skill}/versions")]  # newest first
        if target not in history:
            raise UsageError(f"skill {name} has no version {target}", hint=f"See `z skills versions {name}`.")
        older = history[history.index(target) + 1 :]
        if not older:
            raise UsageError(
                f"{name} v{target} has no earlier version to compare with",
                hint=f"Pass --from (see `z skills versions {name}`).",
            )
        from_ = older[0]
    old = api.get(f"/skills/{skill}/versions/{from_}")
    new = api.get(f"/skills/{skill}/versions/{target}")
    text = diff_text(name, old, new)
    if as_json:
        output.print_json({"skill": name, "from": _version_meta(old), "to": _version_meta(new), "diff": text})
        return
    header = Text(f"{name}  v{old['version']} → v{new['version']}", style="z.b")
    header.append(f"  {new['origin']} · {when(new['created_at'])}", style="z.d")
    if new.get("created_by"):
        header.append(f" · {new['created_by']}", style="z.d")
    if not text:
        output.out().print(header)
        output.note("No differences: both versions have the same SKILL.md.")
        return
    body = text.splitlines()
    added = sum(1 for line in body if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in body if line.startswith("-") and not line.startswith("---"))
    header.append(f"  +{added}", style="z.ok")
    header.append(f" -{removed}", style="z.err")
    output.out().print(Group(header, Text(""), diff_view(text)))


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
    output.emit(p, as_json, playbook_view)


def playbook_view(p: dict[str, Any]) -> Group:
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
    return Group(*parts)


@command(playbooks_app, "save-from")
def playbooks_save_from(
    mission: Annotated[str, typer.Argument(help="The mission whose plan becomes the playbook.")],
    name: Annotated[str, typer.Option("--name", help="Playbook name (a slug, e.g. weekly-edge-patching).")],
    description: Annotated[
        str | None, typer.Option("--description", "--title", help="Description (default: the mission title).")
    ] = None,
    domain: Annotated[str | None, typer.Option("--domain", help="Domain, e.g. vuln.")] = None,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Save a mission's plan as a new, versioned playbook (steps, connectors, writes, autonomy, budget)."""
    if not confirm(f"Save mission {mission} as playbook {name}?", yes=yes or as_json, default=True):
        raise typer.Exit(1)
    body: dict[str, Any] = {"name": name}
    if description:
        body["description"] = description
    if domain:
        body["domain"] = domain
    p = state.get().api().post(f"/missions/{mission}/save-as-playbook", body)
    if as_json:
        output.print_json(p)
        return
    output.success(f"Saved playbook {p['name']} v{p['version']} from mission {mission}")
    output.out().print(playbook_view(p))


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


IMPORT_KINDS = ("openapi", "graphql", "mcp", "docs", "cli", "browser")
KIND_ALIASES = {"rest": "docs"}  # a docs import drafts a `rest` connector
DOC_SUFFIXES = {".md", ".markdown", ".txt", ".html", ".htm", ".rst"}
CLASS_STYLE = {"read": "z.ok", "write": "z.wait", "destructive": "z.err"}


def _structured(text: str) -> Any:
    """JSON or YAML text as data (None when it is neither)."""
    try:
        return json.loads(text)
    except ValueError:
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return None


def _guess_kind(source: str, is_url: bool, doc: Any) -> str | None:
    if isinstance(doc, dict):
        if doc.get("openapi") or doc.get("swagger"):
            return "openapi"
        if "__schema" in doc or "__schema" in (doc.get("data") or {}):
            return "graphql"
        for key, kind in (("transport", "mcp"), ("cli", "cli"), ("flows", "browser")):
            if key in doc:
                return kind
    path = source.split("?", 1)[0].lower()
    if is_url:
        if path.endswith("/graphql"):
            return "graphql"
        if path.endswith((".json", ".yaml", ".yml")) or "openapi" in path or "swagger" in path:
            return "openapi"
        return None
    return "docs" if Path(path).suffix in DOC_SUFFIXES else None


def _default_name(source: str, is_url: bool) -> str:
    base = (source.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]) if is_url else Path(source).stem
    for word in ("openapi", "swagger", "api", "www"):
        base = re.sub(rf"(^|[.\-_]){word}($|[.\-_])", r"\1\2", base.lower())
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:63] or "imported"


def import_body(
    source: str,
    kind: str | None,
    name: str | None,
    display_name: str | None,
    base_url: str | None,
    data_class: str | None,
) -> dict[str, Any]:
    """The ConnectorImport request for a file or URL (contracts/openapi.yaml)."""
    is_url = source.startswith(("http://", "https://"))
    text: str | None = None
    doc: Any = None
    if not is_url:
        path = Path(source).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise UsageError(f"cannot read {source}: {exc}", hint="Give a readable file or an http(s) URL.") from exc
        doc = _structured(text)
    if kind:
        kind = KIND_ALIASES.get(kind, kind)
        if kind not in IMPORT_KINDS:
            raise UsageError(f"--kind must be one of {', '.join(IMPORT_KINDS)} (rest is docs)")
    else:
        kind = _guess_kind(source, is_url, doc)
        if kind is None:
            raise UsageError(f"cannot tell what {source} is", hint="Pass --kind " + "|".join(IMPORT_KINDS) + ".")
    body: dict[str, Any] = {"source": kind, "name": name or _default_name(source, is_url)}
    if display_name:
        body["display_name"] = display_name
    if base_url:
        body["base_url"] = base_url
    if data_class:
        body["data_class"] = data_class
    if kind in {"openapi", "graphql"}:
        if is_url:
            body["spec_url"] = source
        else:
            body["spec"] = doc if kind == "graphql" and isinstance(doc, dict) else text
    elif kind == "docs":
        if is_url:
            raise UsageError("a docs import reads a file", hint="Save the API documentation to a file and pass it.")
        body["docs"] = text
    elif kind == "mcp":
        if is_url:
            body["mcp"] = {"transport": "http", "url": source}
        elif isinstance(doc, dict):
            body["mcp"] = doc
        else:
            raise UsageError(f"{source}: an MCP import needs the server as JSON or YAML (transport and command or url)")
    else:
        needed = ("cli", "declared") if kind == "cli" else ("flows", "declared")
        if is_url or not isinstance(doc, dict) or any(k not in doc for k in needed):
            raise UsageError(f"a {kind} import reads a JSON or YAML file with {' and '.join(needed)}")
        body.update({k: doc[k] for k in needed})
        if kind == "browser" and "base_url" not in body and doc.get("base_url"):
            body["base_url"] = doc["base_url"]
    return body


@command(connectors_app, "import")
def connectors_import(
    source: Annotated[str, typer.Argument(help="A file (spec, docs, MCP server, cli or browser draft) or a URL.")],
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="openapi, graphql, mcp, docs (or rest), cli or browser; guessed when left out."),
    ] = None,
    name: Annotated[
        str | None, typer.Option("--name", help="Connector name (a slug; default: from the source).")
    ] = None,
    display_name: Annotated[str | None, typer.Option("--display-name")] = None,
    base_url: Annotated[str | None, typer.Option("--base-url", help="Base URL of the tool's API.")] = None,
    data_class: Annotated[str | None, typer.Option("--data-class", help="C1 to C4 (default C2).")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Draft a connector from a spec, docs or MCP server and show its operations with their proposed classes.

    Nothing is saved and nothing is exposed: an admin confirms exposure with `z connectors approve-exposure`."""
    api = state.get().api()
    draft = api.post("/connectors/import", import_body(source, kind, name, display_name, base_url, data_class))
    if as_json:
        output.print_json(draft)
        return
    manifest = draft["manifest"]
    ops = draft["operations"]
    proposed = sum(1 for o in ops if o["proposed_exposed"])
    out = output.out()
    out.print(
        output.kv(
            [
                (
                    "draft",
                    Text(
                        f"{manifest.get('display_name') or manifest['name']}  ({manifest['name']}, "
                        f"{manifest.get('kind')})",
                        style="z.b",
                    ),
                ),
                ("base url", manifest.get("base_url")),
                ("data class", manifest.get("data_class")),
                ("operations", f"{len(ops)} drafted · {proposed} proposed for exposure (reads) · none exposed"),
            ]
        )
    )
    if ops:
        out.print(Text(""))
        out.print(
            output.table(
                ["operation", "class", "classifier", "proposed", "description"],
                [
                    [
                        o["id"],
                        Text(o["class"], style=CLASS_STYLE.get(o["class"], "")),
                        o["classifier_class"],
                        "expose" if o["proposed_exposed"] else "hidden",
                        (o.get("description") or "")[:60],
                    ]
                    for o in ops
                ],
            )
        )
    for warning in draft.get("warnings") or []:
        output.note(f"warning: {warning}", "z.wait")
    output.note(
        f"Nothing is saved: create the connector from this draft in the web app ({api.web_url}/connectors/new) or "
        f"with POST /api/v1/connectors and the --json manifest. Agents see none of its operations until an admin "
        f"runs `z connectors approve-exposure {manifest['name']}`."
    )


RECORD_POLL_S = 3.0


def describe_step(s: dict[str, Any]) -> str:
    """One recorded browser step, as the web recorder lists it."""
    action = str(s.get("action", "?"))
    if action in {"goto", "wait_for_url"}:
        return f"{action} {s.get('url', '')}"
    if action == "press":
        return f"press {s.get('key', '')}"
    if action == "screenshot":
        return f"screenshot {s.get('name', '')}"
    value = f" = {s['value']}" if s.get("value") else ""
    named = f" → {s['name']}" if s.get("name") else ""
    return f"{action} {s.get('selector', '')}{value}{named}"


@command(connectors_app, "record-browser")
def connectors_record_browser(
    connector: Annotated[str, typer.Argument(help="A browser connector.")],
    start_url: Annotated[
        str | None, typer.Option("--start-url", help="Page to open (default: the start page of its flows).")
    ] = None,
    name: Annotated[str | None, typer.Option("--name", help="Name of the flow (default: '<connector> flow').")] = None,
    purpose: Annotated[str, typer.Option("--purpose", help="read or write: the account the page signs in with.")] = (
        "read"
    ),
    follow: Annotated[
        bool, typer.Option("--follow", "-f", help="Print the steps as they are recorded, until the recording ends.")
    ] = False,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> int:
    """Start a browser recording and print where to drive it in the web app (ADR 0016).

    Recording is done in the web app: it shows the live page and saves the finished flow into the connector, where
    its operation stays hidden until the exposure review."""
    if purpose not in {"read", "write"}:
        raise UsageError("--purpose must be read or write")
    api = state.get().api()
    c = api.get(f"/connectors/{connector}")
    if c["kind"] != "browser":
        raise UsageError(f"{c['name']} is a {c['kind']} connector: only browser connectors are recorded")
    flows = (c.get("manifest") or {}).get("flows") or {}
    start = start_url or next((f["start_url"] for f in flows.values() if f.get("start_url")), None) or c.get("base_url")
    if not start:
        raise UsageError("no start page", hint="Pass --start-url.")
    question = (
        f"Open a live page on {c['display_name']} signed in with its {purpose} account? "
        "What you do there acts in the real application."
    )
    if not confirm(question, yes=yes or as_json, default=True):
        raise typer.Exit(1)
    body = {"connector": c["name"], "start_url": start, "name": name or f"{c['display_name']} flow", "purpose": purpose}
    session = api.post("/connectors/record-browser", body)
    link = f"{api.web_url}/connectors/record/{session['id']}" if os.environ.get("Z_WEB_URL") else session["viewer_url"]
    if not as_json:
        output.success(f"Recording {session['id']} started on {c['name']} ({purpose} account)")
        output.out().print(
            output.kv([("record at", Text(link, style="z.acc")), ("expires", when(session["expires_at"]))])
        )
        output.note(
            "Open it in the web app, signed in as you. Finish there, then save the flow into the connector; "
            "it closes after 10 idle minutes."
        )
    if not follow:
        if as_json:
            output.print_json({**session, "viewer_url": link})
        return 0
    seen, last, ended = 0, None, ""
    while True:
        try:
            last = api.get(f"/connectors/recordings/{session['id']}")
        except ApiError as exc:
            if exc.status not in {404, 409}:
                raise
            # 404: finished or cancelled (in the web app); 409: expired, or closed on the browser worker
            ended = "finished or cancelled" if exc.status == 404 else (exc.problem.detail or exc.message)
            break
        for i, step in enumerate(last["steps"][seen:], start=seen + 1):
            if not as_json:
                line = Text(f"  {i:>2}  ", style="z.d")
                line.append(describe_step(step))
                output.out().print(line)
        seen = len(last["steps"])
        time.sleep(RECORD_POLL_S)
    if as_json:
        output.print_json({"recording": {**session, "viewer_url": link}, "state": last, "ended": ended})
        return 0
    output.success(f"Recording {session['id']} ended after {seen} step{'' if seen == 1 else 's'}: {ended}")
    output.note(f"A saved flow shows up as an operation: z connectors operations {c['name']}")
    return 0


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
