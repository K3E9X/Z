"""`z admin`: users, teams, roles, group mapping, sessions, tokens, audit (docs/12 §3).

Every admin write needs a fresh step-up on the server and is audited with before/after values. The CLI
shows what will change before it asks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Group
from rich.text import Text

from z_cli import output, state
from z_cli.client import Api
from z_cli.commands import audit as audit_cmds
from z_cli.commands._common import JsonOpt, LimitOpt, YesOpt, command, confirm, parse_duration
from z_cli.errors import UsageError
from z_cli.render import when

app = typer.Typer(
    help="Administration: users, teams, roles, group mapping, sessions, tokens, audit.", no_args_is_help=True
)
users_app = typer.Typer(help="Users: list, inspect, suspend, revoke sessions.", no_args_is_help=True)
teams_app = typer.Typer(help="Teams.", no_args_is_help=True)
roles_app = typer.Typer(help="Roles, permissions and role bindings.", no_args_is_help=True)
groups_app = typer.Typer(help="GitLab group → role and team mapping (with dry run).", no_args_is_help=True)
sessions_app = typer.Typer(help="Active web sessions and CLI tokens of everyone.", no_args_is_help=True)
tokens_app = typer.Typer(help="Personal API tokens of everyone.", no_args_is_help=True)
audit_app = typer.Typer(help="Audit search and chain verification.", no_args_is_help=True)
app.add_typer(users_app, name="users")
app.add_typer(teams_app, name="teams")
app.add_typer(roles_app, name="roles")
app.add_typer(groups_app, name="groups")
app.add_typer(groups_app, name="mapping", help="Alias of `z admin groups`.", hidden=True)
app.add_typer(sessions_app, name="sessions")
app.add_typer(tokens_app, name="tokens")
app.add_typer(audit_app, name="audit")


def _api() -> Api:
    return state.get().api()


def roles_text(user: dict[str, Any]) -> Text:
    t = Text()
    for i, r in enumerate(user.get("effective_roles") or []):
        if i:
            t.append(", ")
        t.append(r["role"])
        t.append(f" {r['scope']}" if r.get("scope") != "org" else "", style="z.d")
        t.append(f" ({r['source']})", style="z.d")
    return t


# ---------------------------------------------------------------- users


@command(users_app, "list")
def users_list(
    role: Annotated[str | None, typer.Option("--role", help="Only users with this effective role.")] = None,
    status: Annotated[str | None, typer.Option("--status", help="active, suspended or deprovisioned.")] = None,
    query: Annotated[str | None, typer.Option("--query", "-q")] = None,
    limit: LimitOpt = 200,
    as_json: JsonOpt = False,
) -> None:
    """List users with their teams, effective roles and last sign-in."""
    items = list(_api().paginate("/admin/users", {"role": role, "status": status, "q": query}, limit=limit))
    output.emit(
        items,
        as_json,
        lambda data: output.table(
            ["user", "name", "teams", "roles", "last sign-in", "2fa", "status"],
            [
                [
                    Text(u["username"], style="z.b"),
                    u["name"],
                    ", ".join(t["name"] for t in u.get("teams") or []),
                    roles_text(u),
                    when(u.get("last_login_at")),
                    "yes" if u.get("mfa_enforced") else "",
                    Text(u["status"], style="z.ok" if u["status"] == "active" else "z.err"),
                ]
                for u in data
            ],
        ),
    )


@command(users_app, "show")
def users_show(user: Annotated[str, typer.Argument(help="Username or user id.")], as_json: JsonOpt = False) -> None:
    """A user: bindings, sessions, tokens, connections, recent activity."""
    u = _api().get(f"/admin/users/{user}")
    if as_json:
        output.print_json(u)
        return
    parts: list[Any] = [
        output.kv(
            [
                ("user", Text(f"{u['name']}  @{u['username']}", style="z.b")),
                ("id", u["id"]),
                ("email", u.get("email")),
                ("identity", f"{u.get('issuer')} · sub {u.get('sub')}" if u.get("issuer") else None),
                ("status", Text(u["status"], style="z.ok" if u["status"] == "active" else "z.err")),
                ("local account", u.get("local_kind")),
                ("teams", ", ".join(t["name"] for t in u.get("teams") or []) or None),
                ("roles", roles_text(u)),
                ("last sign-in", when(u.get("last_login_at")) or None),
            ]
        )
    ]
    if u.get("bindings"):
        parts += [Text(""), Text("manual bindings", style="z.d"), bindings_table(u["bindings"])]
    if u.get("sessions"):
        parts += [Text(""), Text("sessions", style="z.d"), sessions_table(u["sessions"])]
    if u.get("tokens"):
        parts += [Text(""), Text("API tokens", style="z.d"), tokens_table(u["tokens"])]
    if u.get("recent_activity"):
        parts += [Text(""), Text("recent activity", style="z.d"), audit_cmds.audit_rows(u["recent_activity"][:15])]
    output.out().print(Group(*parts))


def _patch_user(user: str, action: str, reason: str | None, yes: bool, as_json: bool) -> None:
    api = _api()
    target = api.get(f"/admin/users/{user}")
    if not as_json:
        output.err().print(
            Text(
                f"{target['username']}: status {target['status']} → {'suspended' if action == 'suspend' else 'active'}",
                style="z.wait",
            )
        )
    if not confirm(f"{action.title()} {target['username']}?", yes=yes or as_json):
        raise typer.Exit(1)
    body: dict[str, Any] = {"action": action}
    if reason:
        body["reason"] = reason
    result = api.patch(f"/admin/users/{target['id']}", body)
    if as_json:
        output.print_json(result)
    else:
        output.success(f"{result['username']} is {result['status']}")


ReasonOpt = Annotated[str | None, typer.Option("--reason", "-r", help="Why (kept in the audit log).")]


@command(users_app, "suspend")
def users_suspend(user: str, reason: ReasonOpt = None, yes: YesOpt = False, as_json: JsonOpt = False) -> None:
    """Suspend a user (their sessions and tokens stop working)."""
    _patch_user(user, "suspend", reason, yes, as_json)


@command(users_app, "reactivate")
def users_reactivate(user: str, reason: ReasonOpt = None, yes: YesOpt = False, as_json: JsonOpt = False) -> None:
    """Reactivate a suspended user."""
    _patch_user(user, "reactivate", reason, yes, as_json)


def _revoke_user_sessions(user: str, reason: str | None, yes: bool, as_json: bool) -> None:
    api = _api()
    target = api.get(f"/admin/users/{user}")
    count = len(target.get("sessions") or [])
    if not confirm(f"Revoke all {count} session(s) and CLI tokens of {target['username']}?", yes=yes or as_json):
        raise typer.Exit(1)
    api.post(f"/admin/users/{target['id']}/revoke-sessions", {"reason": reason} if reason else {})
    if as_json:
        output.print_json({"user": target["username"], "revoked": True})
    else:
        output.success(f"All sessions of {target['username']} revoked")


@command(users_app, "revoke-sessions")
def users_revoke_sessions(user: str, reason: ReasonOpt = None, yes: YesOpt = False, as_json: JsonOpt = False) -> None:
    """Revoke every session and CLI token of a user."""
    _revoke_user_sessions(user, reason, yes, as_json)


# ---------------------------------------------------------------- teams


def find_team(api: Api, team: str) -> dict[str, Any]:
    teams = api.get("/teams")
    for t in teams:
        if team in (t["id"], t["name"]) or t["name"].lower() == team.lower():
            found: dict[str, Any] = t
            return found
    raise UsageError(f"no team {team!r}", hint="Teams: " + ", ".join(t["name"] for t in teams))


@command(teams_app, "list")
def teams_list(as_json: JsonOpt = False) -> None:
    """List teams."""
    items = _api().get("/teams")
    output.emit(
        items,
        as_json,
        lambda data: output.table(
            ["team", "id", "members", "gitlab groups", "monthly budget"],
            [
                [
                    Text(t["name"], style="z.b"),
                    t["id"],
                    str(t.get("members_count") or 0),
                    ", ".join(t.get("gitlab_groups") or []),
                    str(t.get("monthly_token_budget") or ""),
                ]
                for t in data
            ],
        ),
    )


@command(teams_app, "show")
def teams_show(team: str, as_json: JsonOpt = False) -> None:
    """Show a team."""
    api = _api()
    t = api.get(f"/teams/{find_team(api, team)['id']}")
    output.emit(
        t,
        as_json,
        lambda d: output.kv(
            [
                ("team", Text(d["name"], style="z.b")),
                ("id", d["id"]),
                ("members", str(d.get("members_count") or 0)),
                ("gitlab groups", ", ".join(d.get("gitlab_groups") or []) or None),
                ("monthly budget", str(d["monthly_token_budget"]) if d.get("monthly_token_budget") else None),
            ]
        ),
    )


@command(teams_app, "create")
def teams_create(
    name: str,
    budget: Annotated[int | None, typer.Option("--monthly-budget", help="Monthly token budget.")] = None,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Create a team."""
    if not confirm(f"Create team {name!r}?", yes=yes or as_json):
        raise typer.Exit(1)
    body: dict[str, Any] = {"name": name}
    if budget is not None:
        body["monthly_token_budget"] = budget
    t = _api().post("/teams", body)
    output.emit(t, as_json, lambda d: Text(f"● Team {d['name']} created ({d['id']})", style="z.ok"))


@command(teams_app, "update")
def teams_update(
    team: str,
    name: Annotated[str | None, typer.Option("--name", help="New name.")] = None,
    budget: Annotated[int | None, typer.Option("--monthly-budget", help="Monthly token budget.")] = None,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Rename a team or change its budget."""
    api = _api()
    current = find_team(api, team)
    body: dict[str, Any] = {"name": name or current["name"]}
    if budget is not None:
        body["monthly_token_budget"] = budget
    if not as_json:
        output.err().print(
            Text(f"{current['name']} (budget {current.get('monthly_token_budget')}) → {body}", style="z.wait")
        )
    if not confirm("Save?", yes=yes or as_json):
        raise typer.Exit(1)
    t = api.patch(f"/teams/{current['id']}", body)
    output.emit(t, as_json, lambda d: Text(f"● Team {d['name']} saved", style="z.ok"))


# ---------------------------------------------------------------- roles and bindings


def bindings_table(rows: list[dict[str, Any]]) -> Any:
    return output.table(
        ["binding", "subject", "role", "scope", "source", "expires", "by"],
        [
            [
                b["id"],
                b["subject"],
                b["role"],
                b["scope"],
                b["source"],
                when(b.get("expires_at")) or "never",
                b.get("created_by") or "",
            ]
            for b in rows
        ],
    )


def sessions_table(rows: list[dict[str, Any]]) -> Any:
    return output.table(
        ["session", "user", "kind", "device", "ip", "last seen", "expires"],
        [
            [
                s["id"],
                s["user"],
                s["kind"],
                s.get("device") or "",
                s.get("ip") or "",
                when(s["last_seen_at"]),
                when(s["expires_at"]),
            ]
            for s in rows
        ],
    )


def tokens_table(rows: list[dict[str, Any]]) -> Any:
    return output.table(
        ["token", "user", "name", "scopes", "expires", "last used", "status"],
        [
            [
                t["id"],
                t["user"],
                t["name"],
                ", ".join(t["scopes"]),
                when(t["expires_at"]),
                when(t.get("last_used_at")) or "never",
                Text("revoked", style="z.err") if t.get("revoked_at") else Text("active", style="z.ok"),
            ]
            for t in rows
        ],
    )


@command(roles_app, "list")
def roles_list(as_json: JsonOpt = False) -> None:
    """Built-in and custom roles."""
    items = _api().get("/admin/roles")
    output.emit(
        items,
        as_json,
        lambda data: output.table(
            ["role", "kind", "permissions", "description"],
            [
                [
                    Text(r["name"], style="z.b"),
                    "built-in" if r["builtin"] else "custom",
                    str(len(r["permissions"])),
                    r.get("description") or "",
                ]
                for r in data
            ],
        ),
    )


@command(roles_app, "show")
def roles_show(role: str, as_json: JsonOpt = False) -> None:
    """The permissions of a role."""
    items = _api().get("/admin/roles")
    found = next((r for r in items if role in (r["name"], r["id"])), None)
    if found is None:
        raise UsageError(f"no role {role!r}", hint="Roles: " + ", ".join(r["name"] for r in items))
    output.emit(
        found,
        as_json,
        lambda r: Group(
            Text(f"{r['name']} ({'built-in, read-only' if r['builtin'] else 'custom'})", style="z.b"),
            *[Text(f"  {p}") for p in r["permissions"]],
        ),
    )


@command(roles_app, "create")
def roles_create(
    name: str,
    permission: Annotated[list[str], typer.Option("--permission", "-p", help="resource:action (repeat).")],
    description: Annotated[str | None, typer.Option("--description")] = None,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Create a custom role."""
    if not confirm(f"Create role {name} with {len(permission)} permission(s)?", yes=yes or as_json):
        raise typer.Exit(1)
    body: dict[str, Any] = {"name": name, "permissions": permission}
    if description:
        body["description"] = description
    r = _api().post("/admin/roles", body)
    output.emit(r, as_json, lambda d: Text(f"● Role {d['name']} created", style="z.ok"))


@command(roles_app, "permissions")
def roles_permissions(as_json: JsonOpt = False) -> None:
    """The permission catalog and which built-in roles have each permission."""
    items = _api().get("/admin/permissions")
    output.emit(
        items,
        as_json,
        lambda data: output.table(
            ["permission", "roles", "description"],
            [[p["permission"], ", ".join(p["roles"]), p["description"]] for p in data],
        ),
    )


@command(roles_app, "who-can")
def roles_who_can(permission: str, as_json: JsonOpt = False) -> None:
    """Who holds a permission, where, and through what."""
    data = _api().get("/admin/who-can", {"permission": permission})
    output.emit(
        data,
        as_json,
        lambda d: (
            output.table(["user", "scope", "via"], [[u["user"], u["scope"], u["via"]] for u in d["users"]])
            if d["users"]
            else Text(f"Nobody holds {permission}.", style="z.d")
        ),
    )


@command(roles_app, "bindings")
def roles_bindings(
    user: Annotated[str | None, typer.Option("--user", help="Only this user's bindings.")] = None,
    role: Annotated[str | None, typer.Option("--role")] = None,
    as_json: JsonOpt = False,
) -> None:
    """List role bindings (manual grants and their expiry)."""
    api = _api()
    subject = f"user:{api.get(f'/admin/users/{user}')['id']}" if user else None
    items = api.get("/admin/role-bindings", {"subject": subject, "role": role})
    output.emit(items, as_json, bindings_table)


def _scope(api: Api, team: str | None) -> str:
    return f"team:{find_team(api, team)['id']}" if team else "org"


@command(roles_app, "grant")
def roles_grant(
    user: Annotated[str, typer.Argument(help="Username, or group:<gitlab path>.")],
    role: str,
    team: Annotated[str | None, typer.Option("--team", help="Scope the role to a team (default: org-wide).")] = None,
    expires: Annotated[str | None, typer.Option("--expires", help="e.g. 30d (default: no expiry).")] = None,
    reason: ReasonOpt = None,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Grant a role manually (audited; an admin cannot change their own roles)."""
    api = _api()
    if user.startswith("group:"):
        subject, label = user, user
    else:
        target = api.get(f"/admin/users/{user}")
        subject, label = f"user:{target['id']}", target["username"]
    body: dict[str, Any] = {"subject": subject, "role": role, "scope": _scope(api, team)}
    if expires:
        body["expires_at"] = (datetime.now(UTC) + parse_duration(expires)).isoformat()
    if reason:
        body["reason"] = reason
    if not as_json:
        output.err().print(
            Text(
                f"+ {label}: {role} on {team or 'org'}" + (f" until {when(body['expires_at'])}" if expires else ""),
                style="z.ok",
            )
        )
    if not confirm("Grant it?", yes=yes or as_json):
        raise typer.Exit(1)
    b = api.post("/admin/role-bindings", body)
    output.emit(b, as_json, lambda d: Text(f"● {label} is {d['role']} on {team or 'org'} ({d['id']})", style="z.ok"))


@command(roles_app, "revoke")
def roles_revoke(
    user: str,
    role: str,
    team: Annotated[str | None, typer.Option("--team")] = None,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Remove a manual role binding (GitLab-derived roles change in GitLab or the mapping)."""
    api = _api()
    subject = user if user.startswith("group:") else f"user:{api.get(f'/admin/users/{user}')['id']}"
    scope = _scope(api, team)
    matches = [b for b in api.get("/admin/role-bindings", {"subject": subject, "role": role}) if b["scope"] == scope]
    if not matches:
        raise UsageError(f"{user} has no manual {role} binding on {team or 'org'}")
    if not as_json:
        output.err().print(Text(f"- {user}: {role} on {team or 'org'}", style="z.err"))
    if not confirm("Revoke it?", yes=yes or as_json):
        raise typer.Exit(1)
    for b in matches:
        api.delete(f"/admin/role-bindings/{b['id']}")
    if as_json:
        output.print_json({"revoked": [b["id"] for b in matches]})
    else:
        output.success(f"Revoked {role} from {user} on {team or 'org'}")


# ---------------------------------------------------------------- group mapping


def mapping_view(m: dict[str, Any]) -> Group:
    rows: list[Any] = [
        output.kv([("allowed groups", ", ".join(m["allowed_groups"])), ("no match", m["no_match"])]),
        Text(""),
        output.table(
            ["gitlab group", "role", "scope"], [[g["group"], g["role"], g["scope"]] for g in m["group_mapping"]]
        ),
        Text(""),
        output.table(["gitlab group", "team"], [[g["group"], g["team"]] for g in m["team_mapping"]]),
    ]
    return Group(*rows)


def changes_view(dry: dict[str, Any]) -> Group:
    rows: list[Any] = [Text(f"{dry['users_evaluated']} users evaluated", style="z.d")]
    for c in dry["changes"]:
        t = Text("+ " if c["change"] == "gain" else "- ", style="z.ok" if c["change"] == "gain" else "z.err")
        t.append(c["user"], style="z.b")
        what = c.get("role") or ""
        if c.get("team"):
            what += f" team {c['team']}"
        t.append(f" {what} {c.get('scope') or ''}".rstrip())
        rows.append(t)
    if not dry["changes"]:
        rows.append(Text("No one gains or loses access.", style="z.d"))
    if dry.get("denied_users"):
        rows.append(Text("Would be refused at sign-in: " + ", ".join(dry["denied_users"]), style="z.err"))
    return Group(*rows)


@command(groups_app, "list")
def groups_list(as_json: JsonOpt = False) -> None:
    """The effective group mapping (database overrides config/z.yaml)."""
    output.emit(_api().get("/admin/group-mapping"), as_json, mapping_view)


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) if path.suffix in {".yaml", ".yml"} else json.loads(text)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc
    if isinstance(data, dict) and "auth" in data:
        data = data["auth"]
    if not isinstance(data, dict):
        raise UsageError(f"{path}: expected a mapping object")
    return {k: data[k] for k in ("allowed_groups", "no_match", "group_mapping", "team_mapping") if k in data}


@command(groups_app, "test")
def groups_test(
    user: Annotated[str, typer.Option("--user", help="Username to explain.")],
    file: Annotated[
        Path | None, typer.Option("--file", help="Proposed mapping (YAML or JSON) to test instead.")
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Dry run for one user: their effective roles now, and what a proposed mapping would change for them."""
    api = _api()
    target = api.get(f"/admin/users/{user}")
    body = _load_mapping(file) if file else api.get("/admin/group-mapping")
    dry = api.post("/admin/group-mapping:dry-run", body)
    mine = [c for c in dry["changes"] if c["user"] == target["username"]]
    denied = target["username"] in (dry.get("denied_users") or [])
    if as_json:
        output.print_json(
            {
                "user": target["username"],
                "effective_roles": target["effective_roles"],
                "changes": mine,
                "denied": denied,
            }
        )
        return
    parts: list[Any] = [Text(f"{target['username']} now: ", style="z.b") + roles_text(target)]
    for r in target["effective_roles"]:
        if r.get("via"):
            parts.append(Text(f"  {r['role']} via {r['via']}", style="z.d"))
    parts.append(changes_view({**dry, "changes": mine, "denied_users": [target["username"]] if denied else []}))
    output.out().print(Group(*parts))


@command(groups_app, "dry-run")
def groups_dry_run(
    file: Annotated[Path, typer.Argument(help="Proposed mapping (YAML or JSON).")], as_json: JsonOpt = False
) -> None:
    """Who would gain or lose access with a proposed mapping."""
    dry = _api().post("/admin/group-mapping:dry-run", _load_mapping(file))
    output.emit(dry, as_json, changes_view)


@command(groups_app, "apply")
def groups_apply(file: Path, yes: YesOpt = False, as_json: JsonOpt = False) -> None:
    """Save a new mapping after showing its dry run."""
    api = _api()
    body = _load_mapping(file)
    dry = api.post("/admin/group-mapping:dry-run", body)
    if not as_json:
        output.err().print(changes_view(dry))
    if not confirm("Save this mapping?", yes=yes or as_json):
        raise typer.Exit(1)
    saved = api.put("/admin/group-mapping", body)
    output.emit(saved, as_json, lambda d: Text("● Group mapping saved", style="z.ok"))


# ---------------------------------------------------------------- sessions and tokens


@command(sessions_app, "list")
def admin_sessions_list(
    user: Annotated[str | None, typer.Option("--user")] = None, limit: LimitOpt = 200, as_json: JsonOpt = False
) -> None:
    """Active sessions (web and CLI) with device, IP and last seen."""
    items = list(_api().paginate("/admin/sessions", {"user": user}, limit=limit))
    output.emit(items, as_json, sessions_table)


@command(sessions_app, "revoke")
def admin_sessions_revoke(
    session_id: Annotated[str | None, typer.Argument(help="Session id (or --user for all of a user's).")] = None,
    user: Annotated[str | None, typer.Option("--user", help="Revoke every session of this user.")] = None,
    reason: ReasonOpt = None,
    yes: YesOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Revoke one session, or all sessions of a user."""
    if user:
        _revoke_user_sessions(user, reason, yes, as_json)
        return
    if not session_id:
        raise UsageError("give a session id or --user")
    if not confirm(f"Revoke session {session_id}?", yes=yes or as_json):
        raise typer.Exit(1)
    _api().delete(f"/admin/sessions/{session_id}")
    output.emit(
        {"id": session_id, "revoked": True}, as_json, lambda d: Text(f"● Session {session_id} revoked", style="z.ok")
    )


@command(tokens_app, "list")
def admin_tokens_list(
    user: Annotated[str | None, typer.Option("--user")] = None, limit: LimitOpt = 200, as_json: JsonOpt = False
) -> None:
    """Personal API tokens (scopes, expiry, last use)."""
    items = list(_api().paginate("/admin/tokens", {"user": user}, limit=limit))
    output.emit(items, as_json, tokens_table)


@command(tokens_app, "revoke")
def admin_tokens_revoke(token_id: str, yes: YesOpt = False, as_json: JsonOpt = False) -> None:
    """Revoke an API token."""
    if not confirm(f"Revoke token {token_id}?", yes=yes or as_json):
        raise typer.Exit(1)
    _api().delete(f"/admin/tokens/{token_id}")
    output.emit({"id": token_id, "revoked": True}, as_json, lambda d: Text(f"● Token {token_id} revoked", style="z.ok"))


# ---------------------------------------------------------------- audit


@command(audit_app, "search")
def admin_audit_search(
    actor: audit_cmds.ActorOpt = None,
    action: audit_cmds.ActionOpt = None,
    target_type: Annotated[str | None, typer.Option("--target-type")] = None,
    target_id: Annotated[str | None, typer.Option("--target")] = None,
    result: Annotated[str | None, typer.Option("--result")] = None,
    ip: Annotated[str | None, typer.Option("--ip")] = None,
    since: audit_cmds.SinceOpt = None,
    until: audit_cmds.UntilOpt = None,
    limit: LimitOpt = 100,
    as_json: JsonOpt = False,
) -> None:
    """Search the full audit log (`--actor alice --since 7d`)."""
    audit_cmds.search(
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


@command(audit_app, "verify")
def admin_audit_verify(as_json: JsonOpt = False) -> int:
    """Verify the hash chain and the signed checkpoints."""
    return audit_cmds.verify(as_json)
