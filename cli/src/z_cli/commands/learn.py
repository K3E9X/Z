"""Evals, the learning loop, objectives and assignments, and the agent builder (docs/10 §3)."""

from __future__ import annotations

import time
from typing import Annotated, Any

import typer
from rich.text import Text

from z_cli import output, state
from z_cli.commands._common import JsonOpt, LimitOpt, command
from z_cli.errors import UsageError
from z_cli.render import when

learn_app = typer.Typer(help="Learning proposals: review, apply, reject, try.", no_args_is_help=True)
objectives_app = typer.Typer(help="Standing objectives of an agent.", no_args_is_help=True)
top_level = typer.Typer()

ReasonOpt = Annotated[str | None, typer.Option("--reason", "-r", help="Why (recorded in the audit log).")]


# ---------------------------------------------------------------- evals


def _scores(run: dict[str, Any]) -> str:
    s = run.get("scores") or {}
    if not s:
        return str(run["status"])
    rubric = f" · rubric {s['rubric_avg']:.2f}/2" if s.get("rubric_avg") is not None else ""
    return f"{s['cases_passed']}/{s['cases_total']} cases · {s['checks_passed']}/{s['checks_total']} checks{rubric}"


@command(top_level, "eval")
def eval_run(
    target: Annotated[str, typer.Argument(help="@agent, agent:<name> or skill:<name>.")],
    models: Annotated[str | None, typer.Option("--models", help="Comma-separated aliases to compare.")] = None,
    cases: Annotated[str | None, typer.Option("--cases", help="Comma-separated case ids (default: all).")] = None,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Wait for the grades.")] = True,
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds to wait.")] = 1800,
    as_json: JsonOpt = False,
) -> None:
    """Run an eval suite as real missions (writes captured, never applied) and show the grades."""
    ref = f"agent:{target[1:]}" if target.startswith("@") else (target if ":" in target else f"agent:{target}")
    body: dict[str, Any] = {"target": ref}
    if models:
        body["models"] = [m.strip() for m in models.split(",") if m.strip()]
    if cases:
        body["cases"] = [c.strip() for c in cases.split(",") if c.strip()]
    api = state.get().api()
    runs = api.post("/evals", body)
    if wait:
        deadline = time.monotonic() + timeout
        while any(r["status"] in {"queued", "running"} for r in runs) and time.monotonic() < deadline:
            time.sleep(3)
            runs = [api.get(f"/evals/{r['id']}") for r in runs]
    if as_json:
        output.print_json(runs)
        return
    rows = [
        [
            r["id"],
            r["model"],
            Text(r["status"]),
            _scores(r),
            "yes" if r.get("passed") else "no" if r.get("passed") is False else "—",
        ]
        for r in runs
    ]
    output.out().print(output.table(["Run", "Model", "Status", "Scores", "Passed"], rows))
    for r in runs:
        failed = [c for c in r.get("cases", []) if not c.get("passed") and r["status"] == "succeeded"]
        for case in failed[:10]:
            bad = ", ".join(f"{c['type']} ({c.get('detail', '')[:60]})" for c in case["checks"] if not c["ok"])
            output.note(f"{r['model']} · {case['id']}: {bad or 'a rubric item scored 0'}")


# ---------------------------------------------------------------- learning proposals


def _proposal_rows(items: list[dict[str, Any]]) -> list[list[Any]]:
    rows = []
    for p in items:
        after = p.get("eval_after") or {}
        score = f"{after.get('cases_passed')}/{after.get('cases_total')}" if after else "—"
        rows.append([p["id"], p["kind"], p["target"], p["title"], p["status"], score, when(p.get("created_at"))])
    return rows


@command(learn_app, "proposals")
def proposals(
    status: Annotated[str | None, typer.Option("--status", help="e.g. ready, evaluating, applied.")] = None,
    limit: LimitOpt = 50,
    as_json: JsonOpt = False,
) -> None:
    """List learning proposals."""
    items = list(state.get().api().paginate("/proposals", {"status": status} if status else None, limit=limit))
    if as_json:
        output.print_json(items)
        return
    output.out().print(
        output.table(["Proposal", "Kind", "Target", "Title", "Status", "Eval", "Created"], _proposal_rows(items))
    )


@command(learn_app, "show")
def proposal_show(proposal_id: str, as_json: JsonOpt = False) -> None:
    """Show a proposal: diff, evidence, before/after scores, regressions, cost delta."""
    p = state.get().api().get(f"/proposals/{proposal_id}")
    if as_json:
        output.print_json(p)
        return
    before, after = p.get("eval_before") or {}, p.get("eval_after") or {}
    output.out().print(
        output.kv(
            [
                ("proposal", Text(p["id"], style="z.b")),
                ("kind", f"{p['kind']} → {p['target']}"),
                ("title", p["title"]),
                ("status", p["status"]),
                ("evidence", f"{len(p['evidence'])} signals"),
                ("before", f"{before.get('cases_passed')}/{before.get('cases_total')}" if before else None),
                ("after", f"{after.get('cases_passed')}/{after.get('cases_total')}" if after else None),
                ("regressions", ", ".join(p["regressions"]) or "none"),
                ("cost", f"{p['cost_delta']:+.0%}" if p.get("cost_delta") is not None else None),
                ("rationale", p.get("rationale")),
            ]
        )
    )
    output.out().print(Text(p["diff"]))


def _decide(proposal_id: str, verb: str, reason: str | None, as_json: bool) -> None:
    result = state.get().api().post(f"/proposals/{proposal_id}/{verb}", {"reason": reason} if reason else {})
    if as_json:
        output.print_json(result)
        return
    if verb == "try":
        output.success(f"Trying the candidate in mission {result['id']} (writes are captured, never applied).")
    else:
        output.success(f"{proposal_id} is {result['status']}.")


@command(learn_app, "apply")
def proposal_apply(proposal_id: str, reason: ReasonOpt = None, as_json: JsonOpt = False) -> None:
    """Apply a ready proposal (new version, rollback anytime)."""
    _decide(proposal_id, "apply", reason, as_json)


@command(learn_app, "reject")
def proposal_reject(proposal_id: str, reason: ReasonOpt = None, as_json: JsonOpt = False) -> None:
    """Reject a proposal."""
    _decide(proposal_id, "reject", reason, as_json)


@command(learn_app, "try")
def proposal_try(proposal_id: str, as_json: JsonOpt = False) -> None:
    """Run the candidate on one recent mission of the agent."""
    _decide(proposal_id, "try", None, as_json)


@command(learn_app, "signals")
def signals(
    agent: Annotated[str | None, typer.Option("--agent", help="Only this agent.")] = None,
    kind: Annotated[str | None, typer.Option("--kind", help="e.g. action_rejected.")] = None,
    limit: LimitOpt = 50,
    as_json: JsonOpt = False,
) -> None:
    """Recent learning signals (edits, rejections, answers, failures, guardrail hits)."""
    params = {k: v for k, v in {"agent": agent.removeprefix("@") if agent else None, "kind": kind}.items() if v}
    items = list(state.get().api().paginate("/signals", params, limit=limit))
    if as_json:
        output.print_json(items)
        return
    rows = [[s["id"], s["kind"], s.get("agent") or "—", s.get("mission_id") or "—", when(s["ts"])] for s in items]
    output.out().print(output.table(["Signal", "Kind", "Agent", "Mission", "When"], rows))


# ---------------------------------------------------------------- objectives and assignments


def _agent(ref: str) -> str:
    return ref.removeprefix("@")


@command(objectives_app, "list")
def objectives_list(
    agent: Annotated[str, typer.Option("--agent", "-a", help="@handle or name.")], as_json: JsonOpt = False
) -> None:
    """Objectives of an agent with their status and last value."""
    items = state.get().api().get(f"/agents/{_agent(agent)}/objectives")
    if as_json:
        output.print_json(items)
        return
    rows = [
        [
            o["id"],
            o["title"],
            o["status"],
            "—" if o.get("last_value") is None else f"{o['last_value']:g}",
            f"{o['metric']['target']:g}",
            when(o.get("last_checked_at")),
        ]
        for o in items
    ]
    output.out().print(output.table(["Objective", "Title", "Status", "Value", "Target", "Checked"], rows))


@command(objectives_app, "show")
def objectives_show(objective_id: str, as_json: JsonOpt = False) -> None:
    """History of an objective's checks."""
    history = state.get().api().get(f"/objectives/{objective_id}/history")
    if as_json:
        output.print_json(history)
        return
    rows = [
        [when(h["ts"]), f"{h['value']:g}", f"{h['target']:g}", h["status"], (h.get("explanation") or "")[:80]]
        for h in history
    ]
    output.out().print(output.table(["When", "Value", "Target", "Status", "Explanation"], rows))


@command(objectives_app, "add")
def objectives_add(
    agent: Annotated[str, typer.Option("--agent", "-a", help="@handle or name.")],
    title: Annotated[str, typer.Option("--title", help="What the agent looks after.")],
    query: Annotated[
        str,
        typer.Option(
            "--metric",
            help="Z metric: untriaged_findings, open_proposals, stale_tickets, overdue_tickets, mission_failures.",
        ),
    ],
    target: Annotated[float, typer.Option("--target", help="Target value.")] = 0,
    direction: Annotated[str, typer.Option("--direction", help="down or up.")] = "down",
    cadence: Annotated[str, typer.Option("--cadence", help="Cron (UTC).")] = "0 */4 * * *",
    playbook: Annotated[list[str] | None, typer.Option("--playbook", help="Allowed playbook (repeat).")] = None,
    auto_start: Annotated[
        bool, typer.Option("--auto-start-read-only", help="Start read-only playbooks by itself.")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Give an agent a standing objective."""
    if direction not in {"down", "up"}:
        raise UsageError("--direction is down or up")
    body = {
        "title": title,
        "metric": {"source": "z", "query": query, "target": target, "direction": direction},
        "cadence": cadence,
        "allowed_playbooks": playbook or [],
        "mode": "auto_start_read_only" if auto_start else "suggest",
    }
    created = state.get().api().post(f"/agents/{_agent(agent)}/objectives", body)
    if as_json:
        output.print_json(created)
        return
    output.success(f"Objective {created['id']} added to @{created['agent']}; first check within a minute.")


@command(objectives_app, "pause")
def objectives_pause(
    objective_id: str,
    resume: Annotated[bool, typer.Option("--resume", help="Resume instead.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Pause (or resume) an objective."""
    updated = state.get().api().patch(f"/objectives/{objective_id}", {"paused": not resume})
    if as_json:
        output.print_json(updated)
        return
    output.success(f"{objective_id} is {updated['status']}.")


@command(objectives_app, "check")
def objectives_check(objective_id: str, as_json: JsonOpt = False) -> None:
    """Measure an objective now."""
    check = state.get().api().post(f"/objectives/{objective_id}/evaluate", {})
    if as_json:
        output.print_json(check)
        return
    output.out().print(Text(f"{check['status']}: {check.get('explanation') or ''}"))


@command(top_level, "assign")
def assign(
    agent: Annotated[str, typer.Argument(help="@handle or name.")],
    objects: Annotated[list[str], typer.Argument(help="kind:ref, e.g. jira_project:SECOPS group:infra/")],
    role: Annotated[str, typer.Option("--role", help="owner or watcher.")] = "owner",
    as_json: JsonOpt = False,
) -> None:
    """Make an agent responsible for objects (repos, Jira projects, products...)."""
    api = state.get().api()
    made = []
    for item in objects:
        kind, sep, ref = item.partition(":")
        if not sep or not ref:
            raise UsageError(f"{item!r}: use kind:ref, e.g. jira_project:SECOPS")
        made.append(
            api.post(f"/agents/{_agent(agent)}/assignments", {"object_kind": kind, "object_ref": ref, "role": role})
        )
    if as_json:
        output.print_json(made)
        return
    for a in made:
        output.success(f"@{a['agent']} {a['role']} of {a['object_kind']}:{a['object_ref']}")


# ---------------------------------------------------------------- agent builder


def draft_agent(description: str, team: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"description": description}
    if team:
        body["team"] = team
    result: dict[str, Any] = state.get().api().post("/agents/draft", body)
    return result
