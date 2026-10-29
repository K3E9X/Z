"""Audit log: list (search), export (JSON lines, CSV, syslog), verify the hash chain (docs/12 §4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.text import Text

from z_cli import output, state
from z_cli.commands._common import JsonOpt, LimitOpt, command, since_param
from z_cli.errors import CliError, UsageError
from z_cli.render import when

app = typer.Typer(help="Audit log: search, export, verify the chain.", no_args_is_help=True)

ActorOpt = Annotated[str | None, typer.Option("--actor", help="Username (or system:…, agent:…).")]
ActionOpt = Annotated[str | None, typer.Option("--action", help="Action prefix, e.g. action. or auth.login.")]
SinceOpt = Annotated[str | None, typer.Option("--since", help="7d, 12h, or an RFC 3339 time.")]
UntilOpt = Annotated[str | None, typer.Option("--until", help="RFC 3339 time.")]


def audit_rows(items: list[dict[str, Any]]) -> Any:
    def result(r: str) -> Text:
        return Text(r, style={"success": "z.ok", "denied": "z.wait", "error": "z.err"}.get(r, ""))

    return output.table(
        ["seq", "time", "actor", "action", "target", "result", "ip", "detail"],
        [
            [
                str(e["seq"]),
                when(e["ts"]),
                e["actor"],
                e["action"],
                f"{e['target_type']}:{e.get('target_id') or ''}",
                result(e["result"]),
                e.get("actor_ip") or "",
                json.dumps(e.get("after"), default=str)[:60] if e.get("after") is not None else "",
            ]
            for e in items
        ],
    )


def search(
    *,
    actor: str | None,
    action: str | None,
    target_type: str | None,
    target_id: str | None,
    result: str | None,
    ip: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    as_json: bool,
) -> None:
    params = {
        "actor": actor,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "result": result,
        "ip": ip,
        "from": since_param(since),
        "to": since_param(until) if until else None,
    }
    items = list(state.get().api().paginate("/audit", params, limit=limit, page_size=200))
    if as_json:
        output.print_json(items)
    elif not items:
        output.note("No audit events match.")
    else:
        output.out().print(audit_rows(items))


@command(app, "list")
def audit_list(
    actor: ActorOpt = None,
    action: ActionOpt = None,
    target_type: Annotated[str | None, typer.Option("--target-type")] = None,
    target_id: Annotated[str | None, typer.Option("--target")] = None,
    result: Annotated[str | None, typer.Option("--result", help="success, denied or error.")] = None,
    ip: Annotated[str | None, typer.Option("--ip")] = None,
    since: SinceOpt = None,
    until: UntilOpt = None,
    limit: LimitOpt = 50,
    as_json: JsonOpt = False,
) -> None:
    """Search audit events, newest first (your own unless you are an auditor or admin)."""
    search(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        ip=ip,
        since=since,
        until=until,
        limit=limit,
        as_json=as_json,
    )


def export(fmt: str, since: str | None, until: str | None, out_path: Path | None) -> int:
    if fmt not in {"json", "csv", "syslog"}:
        raise UsageError("--format must be json, csv or syslog")
    api = state.get().api()
    params = {"format": fmt, "from": since_param(since), "to": since_param(until) if until else None}
    lines = 0
    sink = out_path.open("wb") if out_path else sys.stdout.buffer
    try:
        with api.stream("GET", "/audit/export", params=params) as response:
            for chunk in response.iter_bytes():
                sink.write(chunk)
                lines += chunk.count(b"\n")
    finally:
        if out_path:
            sink.close()
        else:
            sink.flush()
    if out_path:
        output.success(f"Exported {lines} lines to {out_path} (the export itself is audited)")
    return 0


@command(app, "export")
def audit_export(
    fmt: Annotated[str, typer.Option("--format", help="json (JSON lines), csv or syslog.")] = "json",
    since: SinceOpt = None,
    until: UntilOpt = None,
    out_path: Annotated[Path | None, typer.Option("--output", "-o", help="File (default: stdout).")] = None,
) -> int:
    """Export the audit log (`z audit export --since 30d > audit.jsonl`). Needs a fresh step-up."""
    return export(fmt, since, until, out_path)


def verify(as_json: bool) -> int:
    result = state.get().api().post("/admin/audit:verify", {})
    if as_json:
        output.print_json(result)
    elif result["ok"]:
        output.success(
            f"Audit chain intact: {result['checked']} events, {result['checkpoints_verified']} signed checkpoints verified"
        )
    else:
        raise CliError(
            f"Audit chain BROKEN at seq {result.get('first_bad_seq')}: {result.get('detail') or 'hash mismatch'} "
            f"({result['checked']} events checked)",
            hint="Someone changed or removed audit rows. Escalate to your security team.",
        )
    return 0 if result["ok"] else 1


@command(app, "verify")
def audit_verify(as_json: JsonOpt = False) -> int:
    """Check the whole hash chain and the signed checkpoints."""
    return verify(as_json)
