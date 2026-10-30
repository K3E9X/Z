"""`z login`: GitLab OIDC through Z (docs/12 §1 "CLI").

1. `GET /auth/cli/config` tells the CLI the issuer, the public CLI client id, the scopes and whether to use
   the device flow.
2. OAuth device authorization grant (RFC 8628, GitLab >= 17.9) against GitLab; on older GitLab (no
   `device_authorization_endpoint`) or when the server disables it: authorization code + PKCE with the fixed
   loopback redirect (`http://127.0.0.1:8765/callback`).
3. `POST /auth/cli/exchange` swaps the GitLab tokens for a Z CLI token (30 days, bound to the device name).
   The GitLab tokens are dropped right after.
In `auth.mode: dev` the exchange signs in the local dev user directly.
"""

from __future__ import annotations

import base64
import contextlib
import getpass
import hashlib
import json
import secrets
import socket
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from z_cli.client import Api
from z_cli.errors import CliError, ExitCode

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
LOGIN_TIMEOUT_S = 600


class LoginError(CliError):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message, exit_code=ExitCode.AUTH, hint=hint)


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


Notify = Callable[[str], None]


def default_device_name() -> str:
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "user"
    return f"z-cli {user}@{socket.gethostname()}"[:255]


def _form_post(http: httpx.Client, url: str, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
    try:
        response = http.post(url, data=data, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise LoginError(f"GitLab is not reachable at {url}: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {"error": "invalid_response", "error_description": response.text[:200]}
    return response.status_code, body if isinstance(body, dict) else {}


def discovery(http: httpx.Client, issuer: str) -> dict[str, Any]:
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        response = http.get(url)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LoginError(f"Cannot read GitLab's OpenID configuration at {url}: {exc}") from exc
    if not isinstance(data, dict) or "token_endpoint" not in data:
        raise LoginError(f"{url} is not an OpenID configuration")
    return data


def start_device_flow(http: httpx.Client, endpoint: str, client_id: str, scopes: list[str]) -> DeviceCode | None:
    """None when GitLab does not offer the device grant to this client (fall back to PKCE)."""
    status, body = _form_post(http, endpoint, {"client_id": client_id, "scope": " ".join(scopes)})
    if status in (404, 405) or body.get("error") in {"unsupported_grant_type", "unauthorized_client"}:
        return None
    if status >= 400 or "device_code" not in body:
        raise LoginError(
            f"GitLab refused the device authorization: {body.get('error_description') or body.get('error') or status}"
        )
    return DeviceCode(
        device_code=str(body["device_code"]),
        user_code=str(body["user_code"]),
        verification_uri=str(body["verification_uri"]),
        verification_uri_complete=body.get("verification_uri_complete"),
        expires_in=int(body.get("expires_in") or 300),
        interval=int(body.get("interval") or 5),
    )


def poll_device_token(
    http: httpx.Client,
    token_endpoint: str,
    client_id: str,
    device: DeviceCode,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    deadline = clock() + device.expires_in
    interval = max(1, device.interval)
    while True:
        sleep(interval)
        status, body = _form_post(
            http,
            token_endpoint,
            {"grant_type": DEVICE_GRANT, "device_code": device.device_code, "client_id": client_id},
        )
        if status < 400 and "access_token" in body:
            return body
        error = body.get("error")
        if error == "authorization_pending":
            pass
        elif error == "slow_down":
            interval += 5  # RFC 8628 §3.5
        elif error == "access_denied":
            raise LoginError("Sign-in was denied in GitLab.")
        elif error == "expired_token":
            raise LoginError("The device code expired before it was approved.", hint="Run `z login` again.")
        else:
            raise LoginError(f"GitLab refused the sign-in: {body.get('error_description') or error or status}")
        if clock() > deadline:
            raise LoginError("The device code expired before it was approved.", hint="Run `z login` again.")


# ---------------------------------------------------------------- authorization code + PKCE (loopback)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class _Callback(BaseHTTPRequestHandler):
    result: dict[str, str] | None = None
    path_expected = "/callback"
    done = threading.Event()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != self.path_expected:
            self.send_response(404)
            self.end_headers()
            return
        type(self).result = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        ok = "code" in type(self).result  # type: ignore[operator]
        body = (
            b"<html><body><h3>Signed in to Z.</h3><p>You can close this tab and go back to the terminal.</p></body></html>"
            if ok
            else b"<html><body><h3>Sign-in failed.</h3><p>Go back to the terminal for details.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        type(self).done.set()

    def log_message(self, format: str, *args: Any) -> None:
        return


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pkce_login(
    http: httpx.Client,
    meta: dict[str, Any],
    client_id: str,
    scopes: list[str],
    redirect_uri: str,
    notify: Notify,
    *,
    open_browser: bool = True,
    timeout_s: float = LOGIN_TIMEOUT_S,
) -> dict[str, Any]:
    target = urlparse(redirect_uri)
    if target.hostname not in {"127.0.0.1", "localhost"} or not target.port:
        raise LoginError(f"the loopback redirect {redirect_uri} must be http://127.0.0.1:<port>/<path>")
    handler: type[_Callback] = type(
        "Callback", (_Callback,), {"result": None, "path_expected": target.path or "/", "done": threading.Event()}
    )
    try:
        server = HTTPServer((target.hostname, target.port), handler)
    except OSError as exc:
        raise LoginError(f"Cannot listen on {target.hostname}:{target.port} for the sign-in redirect: {exc}") from exc
    verifier, challenge = pkce_pair()
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    url = f"{meta['authorization_endpoint']}?{query}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        notify(f"Open this URL to sign in with GitLab:\n  {url}")
        if open_browser:
            with contextlib.suppress(Exception):
                webbrowser.open(url)
        if not handler.done.wait(timeout_s):
            raise LoginError("No sign-in arrived from the browser in time.", hint="Run `z login` again.")
    finally:
        server.shutdown()
        server.server_close()
    result: dict[str, str] = handler.result or {}
    if result.get("state") != state:
        raise LoginError("The sign-in redirect carried the wrong state; refusing it.")
    if "error" in result:
        raise LoginError(f"GitLab refused the sign-in: {result.get('error_description') or result['error']}")
    status, body = _form_post(
        http,
        meta["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": result.get("code", ""),
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    if status >= 400 or "access_token" not in body:
        raise LoginError(f"GitLab refused the code exchange: {body.get('error_description') or body.get('error')}")
    if body.get("id_token") and _decode_jwt_claims(str(body["id_token"])).get("nonce") != nonce:
        raise LoginError("The ID token does not carry this sign-in's nonce; refusing it.")
    return body


# ---------------------------------------------------------------- the whole flow


def login(
    api: Api,
    notify: Notify,
    *,
    device_name: str | None = None,
    open_browser: bool = True,
    force_pkce: bool = False,
    http: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Returns the `CliToken` response of Z (token, expires_at, user)."""
    config = api.get("/auth/cli/config", feature="CLI sign-in (GET /auth/cli/config)")
    name = device_name or default_device_name()
    if config.get("mode") == "dev":
        notify("This server runs in auth mode dev: signing in as its local development user.")
        result: dict[str, Any] = api.post("/auth/cli/exchange", {"dev": True, "device_name": name})
        return result
    issuer, client_id = str(config.get("issuer_url") or ""), str(config.get("client_id") or "")
    if not issuer or not client_id:
        raise LoginError(
            "GitLab sign-in is not configured on this server yet.", hint="An admin sets it in Admin → Sign-in."
        )
    scopes = list(config.get("scopes") or ["openid", "profile", "email"])
    own_http = http is None
    client = http or httpx.Client(timeout=20.0, follow_redirects=False, headers={"User-Agent": "z-cli"})
    try:
        meta = discovery(client, issuer)
        tokens: dict[str, Any] | None = None
        endpoint = meta.get("device_authorization_endpoint")
        if config.get("device_flow", True) and endpoint and not force_pkce:
            device = start_device_flow(client, str(endpoint), client_id, scopes)
            if device is not None:
                where = device.verification_uri_complete or device.verification_uri
                notify(
                    f"To sign in, open {device.verification_uri} and enter the code {device.user_code}"
                    + (f"\n  (or open {device.verification_uri_complete})" if device.verification_uri_complete else "")
                )
                if open_browser:
                    with contextlib.suppress(Exception):
                        webbrowser.open(where)
                notify("Waiting for GitLab…")
                tokens = poll_device_token(client, str(meta["token_endpoint"]), client_id, device, sleep=sleep)
        if tokens is None:
            tokens = pkce_login(
                client, meta, client_id, scopes, str(config.get("loopback_redirect")), notify, open_browser=open_browser
            )
    finally:
        if own_http:
            client.close()
    body = {"gitlab_access_token": tokens["access_token"], "device_name": name}
    if tokens.get("id_token"):
        body["id_token"] = tokens["id_token"]
    exchanged: dict[str, Any] = api.post("/auth/cli/exchange", body)
    return exchanged
