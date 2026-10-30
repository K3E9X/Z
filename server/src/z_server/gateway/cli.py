"""`cli` connector calls (docs/05 §1, ADR 0025): one declared operation = one tool container run.

The argv comes from the reviewed `command` template with the agent's arguments substituted (no shell, values can
never become flags); the tool's environment comes from the connector's `cli.env` with the read or write
credential. The run happens in the executor's tool sandbox: the tool image (digest-pinned and allowlisted in
`executor.tool_images`), read-only root, no capability, uid 65534, the runners' network (outbound only through the
egress proxy), a timeout. stdout is the result (parsed when `cli.output` is json); a non-zero exit is an error
with the end of stderr. Everything leaving the gateway is redacted with the credential's secrets as usual.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from z_common.connectors.cli import CliError, render_argv, render_env

from z_server.db.models import Connector, Operation
from z_server.executors import ExecutorError
from z_server.gateway.auth import AuthMaterial
from z_server.state import AppState

DEFAULT_TIMEOUT_S = 60


class CliRefused(Exception):
    pass


@dataclass
class CliResult:
    ok: bool
    body: Any
    error: str | None


def _command(connector: Connector, op: Operation) -> list[str]:
    for declared in connector.manifest.get("declared") or []:
        if declared.get("id") == op.local_id and isinstance(declared.get("command"), list):
            return [str(c) for c in declared["command"]]
    raise CliRefused(f"{op.id} has no command")


def _auth_values(auth: AuthMaterial) -> dict[str, str]:
    values = {"username": auth.login.get("username", ""), "password": auth.login.get("password", "")}
    if auth.secrets and not values["password"]:
        values["secret"] = auth.secrets[0]  # bearer and api_key: the raw secret
    elif values["password"]:
        values["secret"] = values["password"]
    return {k: v for k, v in values.items() if v}


async def run(
    state: AppState, connector: Connector, op: Operation, args: dict[str, Any], auth: AuthMaterial
) -> CliResult:
    cli = connector.manifest.get("cli") or {}
    image = str(cli.get("image") or "")
    if image not in ((state.config.get("executor") or {}).get("tool_images") or []):
        raise CliRefused("the tool image is not in executor.tool_images")
    if state.sandbox is None:
        raise CliRefused("cli connectors need the docker or kubernetes executor")
    try:
        argv = render_argv(_command(connector, op), args)
        env = render_env({str(k): str(v) for k, v in (cli.get("env") or {}).items()}, _auth_values(auth))
    except CliError as exc:
        raise CliRefused(str(exc)) from exc
    proxy = str((state.config.get("egress") or {}).get("proxy_url") or "")
    if proxy:  # the runners' network has no other way out
        env = {"HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "http_proxy": proxy, **env}
    timeout_s = min(int(cli.get("timeout_s") or DEFAULT_TIMEOUT_S), 300)
    try:
        result = await state.sandbox.run_tool(image, argv, env, timeout_s)
    except ExecutorError as exc:
        return CliResult(False, None, f"the tool sandbox failed: {exc}"[:500])
    if result.timed_out:
        return CliResult(False, None, f"the tool did not finish within {timeout_s} s")
    if result.exit_code != 0:
        tail = (result.stderr or result.stdout).strip()[-1500:]
        return CliResult(
            False, {"exit_code": result.exit_code, "stderr": tail}, f"the tool exited with {result.exit_code}"
        )
    body: Any = result.stdout
    if cli.get("output") == "json":
        try:
            body = json.loads(result.stdout or "null")
        except ValueError:
            return CliResult(False, result.stdout[-1500:], "the tool output is not JSON")
    if result.truncated:
        body = {"output": body, "truncated": True}
    return CliResult(True, body, None)
