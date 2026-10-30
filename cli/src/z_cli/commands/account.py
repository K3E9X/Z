"""login, logout, whoami, and your own sessions and API tokens (docs/12 §1)."""

from __future__ import annotations

import os
import sys
from typing import Annotated, Any

import typer
from rich.console import Group
from rich.text import Text

from z_cli import auth, credentials, output, state
from z_cli.client import Api
from z_cli.commands._common import JsonOpt, YesOpt, command, confirm
from z_cli.config import normalize_url
from z_cli.errors import CliError, UsageError
from z_cli.render import when

app = typer.Typer()
tokens_app = typer.Typer(help="Your personal API tokens (scoped, expiring) for scripts.", no_args_is_help=True)
sessions_app = typer.Typer(help="Your Z sessions (web and CLI).", no_args_is_help=True)


def _notify(text: str) -> None:
    output.err().print(Text(text, style="z.m"))


@command(app, "login")
def login(
    url: Annotated[str | None, typer.Option("--url", help="Z server URL (saved to the active profile).")] = None,
    device_name: Annotated[str | None, typer.Option(help="Name of this device in Admin → Sessions.")] = None,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Do not open a browser; print the URL.")] = False,
    pkce: Annotated[
        bool, typer.Option("--pkce", help="Use the browser + PKCE flow instead of the device flow.")
    ] = False,
    with_token: Annotated[
        bool, typer.Option("--with-token", help="Read a personal API token (zt_…) from stdin instead.")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Sign in with GitLab (device flow) and keep a Z CLI token in the OS keyring."""
    st = state.get()
    if url:
        server = normalize_url(url)
        st.url_override = server
        st.config.set("url", server, st.profile)
        st.config.save()
    server = st.server_url()
    anon = Api(server, None, transport=st.transport)
    try:
        if with_token:
            token = sys.stdin.readline().strip()
            if not token:
                raise UsageError("no token on stdin")
            me = Api(server, token, transport=st.transport).get("/me")
            result: dict[str, Any] = {"token": token, "user": me["user"], "expires_at": None}
        else:
            result = auth.login(anon, _notify, device_name=device_name, open_browser=not no_browser, force_pkce=pkce)
    finally:
        anon.close()
    where = credentials.save(server, str(result["token"]))
    st.close()
    user = result["user"]
    if as_json:
        output.print_json({"url": server, "user": user, "expires_at": result.get("expires_at"), "store": where})
        return
    line = Text()
    line.append("● ", style="z.ok")
    line.append(f"Signed in to {server} as ")
    line.append(str(user.get("username")), style="z.b")
    if result.get("expires_at"):
        line.append(f" · token until {when(result['expires_at'])}", style="z.d")
    line.append(f" · stored in the {'OS keyring' if where == 'keyring' else credentials.token_file()}", style="z.d")
    output.out().print(line)


@command(app, "logout")
def logout(
    all_profiles: Annotated[bool, typer.Option("--all", help="Forget the tokens of every profile.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """End the Z session and forget the token."""
    st = state.get()
    urls = sorted({p.get("url", "") for p in st.config.profiles.values()} - {""}) if all_profiles else [st.server_url()]
    results = []
    for server in urls:
        stored = credentials.load(server)
        revoked = False
        if stored is not None and stored.store != "env":
            api = Api(server, stored.token, transport=st.transport)
            try:
                api.post("/auth/logout")
                revoked = True
            except CliError as exc:
                output.note(
                    f"{server}: the server did not end the session ({exc.message}); forgetting the token anyway."
                )
            finally:
                api.close()
        forgotten = credentials.forget(server)
        results.append({"url": server, "revoked": revoked, "forgotten": forgotten})
    if as_json:
        output.print_json(results)
        return
    for r in results:
        if r["forgotten"] or r["revoked"]:
            output.success(f"Signed out of {r['url']}")
        else:
            output.note(f"Not signed in to {r['url']}.")
    if os.environ.get("Z_TOKEN"):
        output.note("Z_TOKEN is still set in your environment.")


def _roles(user: dict[str, Any]) -> Text:
    text = Text()
    for i, role in enumerate(user.get("effective_roles") or []):
        if i:
            text.append(", ")
        text.append(role["role"], style="z.b")
        scope = role.get("scope", "org")
        text.append(f" ({scope}", style="z.d")
        text.append(f", via {role['via']}" if role.get("via") else f", {role.get('source')}", style="z.d")
        if role.get("expires_at"):
            text.append(f", until {when(role['expires_at'])}", style="z.d")
        text.append(")", style="z.d")
    return text if text.plain else Text("none", style="z.d")


@command(app, "whoami")
def whoami(as_json: JsonOpt = False) -> None:
    """Who you are on this server: user, teams, effective roles, session."""
    st = state.get()
    me = st.api().get("/me")
    if as_json:
        output.print_json({k: v for k, v in me.items() if k != "csrf_token"})
        return
    user = me["user"]
    session = me.get("session") or {}
    workspace = me.get("workspace") or {}
    name = Text(str(user.get("name")), style="z.b")
    name.append(f"  @{user.get('username')}", style="z.d")
    if user.get("local_kind"):
        name.append(f"  local account: {user['local_kind']}", style="z.wait")
    teams = ", ".join(t["name"] for t in user.get("teams") or []) or "none"
    stored = st.token()
    rows = [
        ("user", name),
        ("email", user.get("email")),
        (
            "server",
            f"{st.server_url()} · {workspace.get('name', '')}"
            + (f" · {workspace['environment']}" if workspace.get("environment") else ""),
        ),
        ("teams", teams),
        ("roles", _roles(user)),
        ("2FA enforced", "yes" if user.get("mfa_enforced") else None),
        (
            "session",
            f"{session.get('kind')} · until {when(session.get('expires_at'))}"
            + (f" · step-up {when(session['step_up_at'])}" if session.get("step_up_at") else ""),
        ),
        ("token", f"from {stored.store}" if stored else None),
        ("permissions", str(len(me.get("permissions") or []))),
    ]
    parts: list[Any] = [output.kv(rows)]
    for banner in me.get("banners") or []:
        parts.append(Text(f"! {banner['text']}", style="z.wait"))
    output.out().print(Group(*parts))


# ---------------------------------------------------------------- own tokens and sessions


@command(tokens_app, "list")
def tokens_list(as_json: JsonOpt = False) -> None:
    """List your personal API tokens."""
    rows = state.get().api().get("/me/tokens")
    output.emit(
        rows,
        as_json,
        lambda data: output.table(
            ["id", "name", "scopes", "expires", "last used", "status"],
            [
                [
                    t["id"],
                    t["name"],
                    ", ".join(t["scopes"]),
                    when(t["expires_at"]),
                    when(t.get("last_used_at")) or "never",
                    Text("revoked", style="z.err") if t.get("revoked_at") else Text("active", style="z.ok"),
                ]
                for t in data
            ],
        ),
    )


@command(tokens_app, "create")
def tokens_create(
    name: Annotated[str, typer.Argument(help="What the token is for.")],
    scope: Annotated[list[str], typer.Option("--scope", "-s", help="Permission, e.g. missions:read (repeat).")],
    expires_in_days: Annotated[int, typer.Option("--days", min=1, max=90, help="Days until it expires.")] = 30,
    as_json: JsonOpt = False,
) -> None:
    """Create a scoped personal API token (shown once)."""
    created = state.get().api().post("/me/tokens", {"name": name, "scopes": scope, "expires_in_days": expires_in_days})
    if as_json:
        output.print_json(created)
        return
    output.success(f"Token {created['id']} created, expires {when(created['expires_at'])}. It is shown only once:")
    output.out().print(Text(str(created.get("token")), style="z.b"))


@command(tokens_app, "revoke")
def tokens_revoke(token_id: str, yes: YesOpt = False, as_json: JsonOpt = False) -> None:
    """Revoke one of your API tokens."""
    if not confirm(f"Revoke token {token_id}?", yes=yes or as_json):
        raise typer.Exit(1)
    state.get().api().delete(f"/me/tokens/{token_id}")
    if as_json:
        output.print_json({"id": token_id, "revoked": True})
    else:
        output.success(f"Token {token_id} revoked")


@command(sessions_app, "list")
def sessions_list(as_json: JsonOpt = False) -> None:
    """List your active sessions."""
    rows = state.get().api().get("/me/sessions")
    output.emit(
        rows,
        as_json,
        lambda data: output.table(
            ["id", "kind", "device", "ip", "last seen", "expires", ""],
            [
                [
                    s["id"],
                    s["kind"],
                    s.get("device") or s.get("user_agent") or "",
                    s.get("ip") or "",
                    when(s["last_seen_at"]),
                    when(s["expires_at"]),
                    Text("this one", style="z.ok") if s.get("current") else "",
                ]
                for s in data
            ],
        ),
    )


@command(sessions_app, "revoke")
def sessions_revoke(session_id: str, yes: YesOpt = False, as_json: JsonOpt = False) -> None:
    """End one of your sessions."""
    if not confirm(f"Revoke session {session_id}?", yes=yes or as_json):
        raise typer.Exit(1)
    state.get().api().delete(f"/me/sessions/{session_id}")
    if as_json:
        output.print_json({"id": session_id, "revoked": True})
    else:
        output.success(f"Session {session_id} revoked")


# ---------------------------------------------------------------- personal connections (docs/05 §6)


@command(app, "connect")
def connect(
    connector: Annotated[str, typer.Argument(help="Connector name, e.g. m365.")],
    purpose: Annotated[str, typer.Option("--purpose", help="read, write or both (two consents, two apps).")] = "both",
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Print the consent link instead of opening it.")
    ] = False,
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds to wait for each consent.")] = 300,
    as_json: JsonOpt = False,
) -> None:
    """Connect your own account to a tool (delegated access used only in missions you start)."""
    import time
    import webbrowser

    if purpose not in {"read", "write", "both"}:
        raise UsageError("--purpose is read, write or both")
    api = state.get().api()
    for p in ["read", "write"] if purpose == "both" else [purpose]:
        url = api.post(f"/me/connections/{connector}/oauth/start", {"purpose": p})["redirect_url"]
        if no_browser or not webbrowser.open(url):
            output.note(f"Open this link to give Z {p} access to {connector}:\n{url}")
        else:
            output.note(f"Your browser opened the {p} consent for {connector} (sign in to Z there first if asked).")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = [c for c in api.get("/me/connections") if c["connector"] == connector]
            scopes = (rows[0].get(f"{p}_scopes") if rows else None) or []
            if rows and rows[0]["status"] == "active" and scopes:
                break
            time.sleep(2)
        else:
            raise UsageError(f"no {p} consent received for {connector} within {timeout} s")
    final = [c for c in api.get("/me/connections") if c["connector"] == connector]
    if as_json:
        output.print_json(final[0] if final else None)
        return
    output.success(f"Connected to {connector}.")


@command(app, "connections")
def connections(as_json: JsonOpt = False) -> None:
    """Your personal connections."""
    rows = state.get().api().get("/me/connections")
    if as_json:
        output.print_json(rows)
        return
    output.out().print(
        output.table(
            ["Connection", "Connector", "Status", "Read scopes", "Write scopes", "Last used"],
            [
                [
                    c["id"],
                    c["connector"],
                    c["status"],
                    " ".join(c["read_scopes"]),
                    " ".join(c["write_scopes"]),
                    c.get("last_used_at") or "never",
                ]
                for c in rows
            ],
        )
    )


@command(app, "disconnect")
def disconnect(connection_id: str, yes: YesOpt = False) -> None:
    """Revoke a personal connection (its tokens are deleted)."""
    if not confirm(f"Revoke {connection_id}?", yes=yes):
        return
    state.get().api().delete(f"/me/connections/{connection_id}")
    output.success(f"{connection_id} revoked.")
