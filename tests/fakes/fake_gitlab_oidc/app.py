"""GitLab-like OpenID Connect provider for tests (code + PKCE, refresh, device flow, userinfo, faults)."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

Fault = Literal["bad_issuer", "bad_audience", "expired", "wrong_nonce", "bad_signature"]
FAULTS: tuple[str, ...] = ("bad_issuer", "bad_audience", "expired", "wrong_nonce", "bad_signature")
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
ROLE_CLAIM = "https://gitlab.org/claims/groups/{}"


@dataclass
class Client:
    client_id: str
    client_secret: str | None  # None = public client (PKCE required)
    redirect_uris: list[str]  # exact URIs; fnmatch patterns such as http://127.0.0.1:*/callback are allowed

    def redirect_ok(self, uri: str) -> bool:
        return any(uri == r or fnmatch.fnmatchcase(uri, r) for r in self.redirect_uris)


@dataclass
class User:
    username: str
    name: str
    email: str
    groups: list[str]  # direct memberships (full paths)
    id: int = 0
    blocked: bool = False
    email_verified: bool = True
    roles: dict[str, str] = field(default_factory=dict)  # group path -> owner|maintainer|developer|...
    auth_time: int | None = None  # last interactive sign-in

    def all_groups(self, known: set[str]) -> list[str]:
        """Direct groups plus inherited ones (known subgroups of a direct group), like GitLab userinfo."""
        out = list(self.groups)
        for g in sorted(known):
            if g not in out and any(g.startswith(d + "/") for d in self.groups):
                out.append(g)
        return out


def default_users() -> list[User]:
    def u(name: str, full: str, groups: list[str]) -> User:
        return User(username=name, name=full, email=f"{name}@example.test", groups=groups)

    return [
        u("alice", "Alice Martin", ["cyber/z/z-admins", "cyber/soc-csirt"]),
        u("bob", "Bob Durand", ["cyber/z/z-operators", "cyber/soc-csirt"]),
        u("carol", "Carol Petit", ["cyber/z/z-approvers", "cyber/soc-csirt"]),
        u("dave", "Dave Bernard", ["cyber/z/z-auditors"]),
        u("erin", "Erin Leroy", ["cyber/z/z-builders", "cyber/archi"]),
        u("frank", "Frank Moreau", ["cyber/z/z-approvers", "cyber/soc-csirt"]),
        u("mallory", "Mallory Outsider", ["external/contractors"]),
    ]


def default_clients() -> list[Client]:
    web_id = os.environ.get("FAKE_OIDC_CLIENT_ID", "z-web")
    web_secret = os.environ.get("FAKE_OIDC_CLIENT_SECRET", "z-web-secret")
    web_redirects = os.environ.get(
        "FAKE_OIDC_REDIRECT_URIS",
        "http://localhost:*/api/v1/auth/callback,http://127.0.0.1:*/api/v1/auth/callback,"
        "https://*/api/v1/auth/callback,http://127.0.0.1:8765/callback",
    ).split(",")
    cli_id = os.environ.get("FAKE_OIDC_CLI_CLIENT_ID", "z-cli")
    return [
        Client(web_id, web_secret, [r.strip() for r in web_redirects if r.strip()]),
        Client(cli_id, None, ["http://127.0.0.1:8765/callback", "http://127.0.0.1:*/callback"]),
    ]


@dataclass
class Grant:
    user: str
    client_id: str
    scope: str
    nonce: str | None = None
    auth_time: int = 0


class OIDCState:
    def __init__(self, issuer: str | None) -> None:
        self.lock = threading.RLock()
        self.issuer = issuer
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = hashlib.sha256(str(self.key.public_key().public_numbers().n).encode()).hexdigest()[:16]
        self.config: dict[str, int] = {}
        self.base_clients: list[Client] = []  # extra clients that survive a reset (CLI flags)
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.users: dict[str, User] = {}
            self._next_id = 100
            for user in default_users():
                self.put_user(user)
            self.clients: dict[str, Client] = {c.client_id: c for c in [*default_clients(), *self.base_clients]}
            self.current_user: str | None = None
            self.codes: dict[str, dict[str, Any]] = {}
            self.access_tokens: dict[str, dict[str, Any]] = {}
            self.refresh_tokens: dict[str, Grant] = {}
            self.devices: dict[str, dict[str, Any]] = {}
            self.faults: list[str] = []
            self.sticky_faults: set[str] = set()
            self.events: list[dict[str, Any]] = []
            self.config = {
                "access_token_ttl": int(os.environ.get("FAKE_OIDC_ACCESS_TOKEN_TTL", "7200")),
                "id_token_ttl": int(os.environ.get("FAKE_OIDC_ID_TOKEN_TTL", "120")),
                "device_interval": int(os.environ.get("FAKE_OIDC_DEVICE_INTERVAL", "5")),
                "device_expires_in": int(os.environ.get("FAKE_OIDC_DEVICE_EXPIRES_IN", "300")),
                "code_ttl": 600,
            }

    def put_user(self, user: User) -> User:
        with self.lock:
            old = self.users.get(user.username)
            if old is not None:
                user.id = old.id
            elif not user.id:
                self._next_id += 1
                user.id = self._next_id
            self.users[user.username] = user
            return user

    def known_groups(self) -> set[str]:
        return {g for u in self.users.values() for g in u.groups}

    def event(self, kind: str, **data: Any) -> None:
        self.events.append({"ts": time.time(), "event": kind, **data})

    def take_faults(self) -> set[str]:
        with self.lock:
            once = set(self.faults)
            self.faults.clear()
            return once | self.sticky_faults

    # ---- tokens ----
    def jwk(self) -> dict[str, Any]:
        jwk: dict[str, Any] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}

    def id_token(self, issuer: str, grant: Grant) -> str:
        user = self.users[grant.user]
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": issuer,
            "sub": str(user.id),
            "aud": grant.client_id,
            "exp": now + self.config["id_token_ttl"],
            "iat": now,
            "auth_time": grant.auth_time,
            "sub_legacy": hashlib.sha256(f"{user.id}".encode()).hexdigest(),
            "name": user.name,
            "nickname": user.username,
            "preferred_username": user.username,
            "email": user.email,
            "email_verified": user.email_verified,
            "profile": f"{issuer}/{user.username}",
            "picture": f"{issuer}/uploads/-/system/user/avatar/{user.id}/avatar.png",
            "groups_direct": list(user.groups),
        }
        if grant.nonce is not None:
            claims["nonce"] = grant.nonce
        key: Any = self.key
        faults = self.take_faults()
        if "bad_issuer" in faults:
            claims["iss"] = "https://evil-gitlab.example.test"
        if "bad_audience" in faults:
            claims["aud"] = "another-client"
        if "expired" in faults:
            claims["iat"], claims["exp"] = now - 7200, now - 3600
        if "wrong_nonce" in faults:
            claims["nonce"] = "wrong-" + secrets.token_hex(8)
        if "bad_signature" in faults:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if faults:
            self.event("fault_applied", faults=sorted(faults))
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": self.kid})

    def userinfo(self, issuer: str, user: User) -> dict[str, Any]:
        info: dict[str, Any] = {
            "sub": str(user.id),
            "sub_legacy": hashlib.sha256(f"{user.id}".encode()).hexdigest(),
            "name": user.name,
            "nickname": user.username,
            "preferred_username": user.username,
            "email": user.email,
            "email_verified": user.email_verified,
            "website": "",
            "profile": f"{issuer}/{user.username}",
            "picture": f"{issuer}/uploads/-/system/user/avatar/{user.id}/avatar.png",
            "groups": user.all_groups(self.known_groups()),
        }
        for level in ("owner", "maintainer", "developer"):
            names = [g.rsplit("/", 1)[-1] for g in user.groups if user.roles.get(g, "developer") == level]
            if names:
                info[ROLE_CLAIM.format(level)] = names
        return info

    def issue(self, issuer: str, grant: Grant, with_refresh: bool = True) -> dict[str, Any]:
        access = secrets.token_hex(32)
        ttl = self.config["access_token_ttl"]
        self.access_tokens[access] = {"grant": grant, "exp": time.time() + ttl}
        body: dict[str, Any] = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ttl,
            "scope": grant.scope,
            "created_at": int(time.time()),
        }
        if with_refresh:
            refresh = secrets.token_hex(32)
            self.refresh_tokens[refresh] = grant
            body["refresh_token"] = refresh
        if "openid" in grant.scope.split():
            body["id_token"] = self.id_token(issuer, grant)
        self.event("token_issued", user=grant.user, client_id=grant.client_id)
        return body


def _b64url_sha256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _oauth_error(error: str, description: str = "", status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description or error}, status_code=status)


def create_app(issuer: str | None = None) -> FastAPI:
    state = OIDCState(issuer or os.environ.get("FAKE_OIDC_ISSUER"))
    app = FastAPI(title="fake-gitlab-oidc")
    app.state.fake = state

    def iss(request: Request) -> str:
        return state.issuer or str(request.base_url).rstrip("/")

    def client_auth(request: Request, form: dict[str, str]) -> Client | JSONResponse:
        client_id, secret = form.get("client_id"), form.get("client_secret")
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                raw = base64.b64decode(header[6:]).decode()
            except ValueError:
                return _oauth_error("invalid_client", "bad basic header", 401)
            client_id, _, secret = raw.partition(":")
        client = state.clients.get(client_id or "")
        if client is None:
            return _oauth_error("invalid_client", "unknown client", 401)
        if client.client_secret is not None and not secrets.compare_digest(secret or "", client.client_secret):
            return _oauth_error("invalid_client", "client authentication failed", 401)
        return client

    def active_user(username: str) -> User | None:
        user = state.users.get(username)
        return None if user is None or user.blocked else user

    @app.get("/.well-known/openid-configuration")
    def discovery(request: Request) -> dict[str, Any]:
        base = iss(request)
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "revocation_endpoint": f"{base}/oauth/revoke",
            "introspection_endpoint": f"{base}/oauth/introspect",
            "userinfo_endpoint": f"{base}/oauth/userinfo",
            "jwks_uri": f"{base}/oauth/discovery/keys",
            "device_authorization_endpoint": f"{base}/oauth/authorize_device",
            "scopes_supported": ["api", "read_api", "read_user", "openid", "profile", "email"],
            "response_types_supported": ["code"],
            "response_modes_supported": ["query", "fragment", "form_post"],
            "grant_types_supported": [
                "authorization_code",
                "password",
                "client_credentials",
                "refresh_token",
                DEVICE_GRANT,
            ],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "claim_types_supported": ["normal"],
            "claims_supported": [
                "iss",
                "sub",
                "aud",
                "exp",
                "iat",
                "sub_legacy",
                "name",
                "nickname",
                "preferred_username",
                "email",
                "email_verified",
                "website",
                "profile",
                "picture",
                "groups",
                "groups_direct",
                *(ROLE_CLAIM.format(x) for x in ("owner", "maintainer", "developer")),
            ],
            "code_challenge_methods_supported": ["plain", "S256"],
        }

    @app.get("/oauth/discovery/keys")
    def jwks() -> dict[str, Any]:
        return {"keys": [state.jwk()]}

    @app.get("/oauth/authorize", response_model=None)
    def authorize(request: Request) -> Response:
        q = dict(request.query_params)
        client = state.clients.get(q.get("client_id", ""))
        redirect_uri = q.get("redirect_uri", "")
        if client is None or not client.redirect_ok(redirect_uri):
            return PlainTextResponse("invalid client_id or redirect_uri", status_code=400)

        def back(params: dict[str, str]) -> RedirectResponse:
            if "state" in q:
                params["state"] = q["state"]
            sep = "&" if "?" in redirect_uri else "?"
            return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)

        if q.get("response_type") != "code":
            return back({"error": "unsupported_response_type"})
        challenge, method = q.get("code_challenge"), q.get("code_challenge_method", "plain")
        if client.client_secret is None and not challenge:
            return back({"error": "invalid_request", "error_description": "PKCE required for public clients"})
        if challenge and method not in ("S256", "plain"):
            return back({"error": "invalid_request", "error_description": "unsupported code_challenge_method"})
        with state.lock:
            username = q.get("login_hint") or state.current_user
            user = state.users.get(username or "")
            if user is None:
                return back({"error": "login_required"})
            if user.blocked:
                return back({"error": "access_denied", "error_description": "user is blocked"})
            now = int(time.time())
            prompt = q.get("prompt", "")
            max_age = int(q["max_age"]) if q.get("max_age", "").isdigit() else None
            stale = user.auth_time is None or (max_age is not None and now - user.auth_time > max_age)
            if "login" in prompt.split() or stale:
                if "none" in prompt.split():
                    return back({"error": "login_required"})
                user.auth_time = now
            code = secrets.token_urlsafe(24)
            state.codes[code] = {
                "client_id": client.client_id,
                "redirect_uri": redirect_uri,
                "grant": Grant(
                    user.username, client.client_id, q.get("scope", "openid"), q.get("nonce"), user.auth_time or now
                ),
                "challenge": challenge,
                "method": method,
                "exp": time.time() + state.config["code_ttl"],
            }
            state.event("authorized", user=user.username, client_id=client.client_id)
        return back({"code": code})

    @app.post("/oauth/authorize_device", response_model=None)
    async def authorize_device(request: Request) -> Response:
        form = {k: str(v) for k, v in (await request.form()).items()}
        client = state.clients.get(form.get("client_id", ""))
        if client is None:
            return _oauth_error("invalid_client", "unknown client", 401)
        base = iss(request)
        device_code = secrets.token_hex(20)
        user_code = "-".join(secrets.token_hex(2).upper() for _ in range(2))
        with state.lock:
            state.devices[device_code] = {
                "client_id": client.client_id,
                "scope": form.get("scope", "openid"),
                "user_code": user_code,
                "status": "pending",
                "user": None,
                "exp": time.time() + state.config["device_expires_in"],
                "interval": state.config["device_interval"],
                "last_poll": 0.0,
            }
        return JSONResponse(
            {
                "device_code": device_code,
                "user_code": user_code,
                "verification_uri": f"{base}/oauth/device",
                "verification_uri_complete": f"{base}/oauth/device?user_code={user_code}",
                "expires_in": state.config["device_expires_in"],
                "interval": state.config["device_interval"],
            }
        )

    @app.get("/oauth/device")
    def device_page(user_code: str = "") -> PlainTextResponse:
        return PlainTextResponse(f"Fake GitLab device authorization for code {user_code or '<enter code>'}.")

    @app.post("/oauth/token", response_model=None)
    async def token(request: Request) -> Response:
        form = {k: str(v) for k, v in (await request.form()).items()}
        auth = client_auth(request, form)
        if isinstance(auth, JSONResponse):
            return auth
        client = auth
        grant_type = form.get("grant_type", "")
        base = iss(request)
        with state.lock:
            if grant_type == "authorization_code":
                entry = state.codes.pop(form.get("code", ""), None)
                if entry is None or entry["exp"] < time.time() or entry["client_id"] != client.client_id:
                    return _oauth_error(
                        "invalid_grant",
                        "The provided authorization grant is invalid, expired, "
                        "revoked, does not match the redirection URI used in the authorization "
                        "request, or was issued to another client.",
                    )
                if form.get("redirect_uri") != entry["redirect_uri"]:
                    return _oauth_error("invalid_grant", "redirect_uri mismatch")
                if entry["challenge"]:
                    verifier = form.get("code_verifier", "")
                    expected = _b64url_sha256(verifier) if entry["method"] == "S256" else verifier
                    if not verifier or not secrets.compare_digest(expected, entry["challenge"]):
                        return _oauth_error("invalid_grant", "PKCE verification failed")
                grant: Grant = entry["grant"]
                if active_user(grant.user) is None:
                    return _oauth_error("invalid_grant", "user is blocked or deleted")
                return JSONResponse(state.issue(base, grant))
            if grant_type == "refresh_token":
                old = state.refresh_tokens.pop(form.get("refresh_token", ""), None)
                if old is None or old.client_id != client.client_id:
                    return _oauth_error("invalid_grant", "invalid refresh token")
                if active_user(old.user) is None:
                    state.event("refresh_refused", user=old.user)
                    return _oauth_error("invalid_grant", "user is blocked or deleted")
                return JSONResponse(state.issue(base, Grant(old.user, old.client_id, old.scope, None, old.auth_time)))
            if grant_type == DEVICE_GRANT:
                device = state.devices.get(form.get("device_code", ""))
                if device is None or device["client_id"] != client.client_id:
                    return _oauth_error("invalid_grant", "unknown device code")
                now = time.time()
                if device["exp"] < now:
                    return _oauth_error("expired_token", "The device code has expired")
                if device["status"] == "denied":
                    return _oauth_error("access_denied", "The resource owner denied the request")
                if device["status"] == "used":
                    return _oauth_error("invalid_grant", "device code already used")
                if device["status"] == "pending":
                    too_fast = now - device["last_poll"] < device["interval"]
                    device["last_poll"] = now
                    if too_fast:
                        device["interval"] += 5
                        return _oauth_error("slow_down", "polling too fast")
                    return _oauth_error("authorization_pending", "The authorization request is still pending")
                user = active_user(device["user"])
                if user is None:
                    return _oauth_error("access_denied", "user is blocked or deleted")
                device["status"] = "used"
                user.auth_time = int(now)
                return JSONResponse(
                    state.issue(base, Grant(user.username, client.client_id, device["scope"], None, user.auth_time))
                )
        return _oauth_error("unsupported_grant_type", f"unsupported grant_type {grant_type!r}")

    def bearer_user(request: Request) -> User | None:
        header = request.headers.get("authorization", "")
        tok = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("access_token", "")
        with state.lock:
            entry = state.access_tokens.get(tok)
            if entry is None or entry["exp"] < time.time():
                return None
            return active_user(entry["grant"].user)

    @app.api_route("/oauth/userinfo", methods=["GET", "POST"], response_model=None)
    def userinfo(request: Request) -> Response:
        user = bearer_user(request)
        if user is None:
            return JSONResponse(
                {"error": "invalid_token", "error_description": "The access token is invalid"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="Doorkeeper", error="invalid_token"'},
            )
        return JSONResponse(state.userinfo(iss(request), user))

    @app.get("/api/v4/user", response_model=None)
    def api_user(request: Request) -> Response:
        user = bearer_user(request)
        if user is None:
            return JSONResponse({"message": "401 Unauthorized"}, status_code=401)
        return JSONResponse(
            {
                "id": user.id,
                "username": user.username,
                "name": user.name,
                "email": user.email,
                "state": "blocked" if user.blocked else "active",
            }
        )

    @app.post("/oauth/revoke", response_model=None)
    async def revoke(request: Request) -> Response:
        form = {k: str(v) for k, v in (await request.form()).items()}
        auth = client_auth(request, form)
        if isinstance(auth, JSONResponse):
            return auth
        tok = form.get("token", "")
        with state.lock:
            state.access_tokens.pop(tok, None)
            state.refresh_tokens.pop(tok, None)
        return JSONResponse({})

    # ---- control API -------------------------------------------------------
    def user_json(user: User) -> dict[str, Any]:
        return {**user.__dict__, "all_groups": user.all_groups(state.known_groups())}

    @app.get("/_fake/users")
    def list_users() -> list[dict[str, Any]]:
        with state.lock:
            return [user_json(u) for u in state.users.values()]

    @app.post("/_fake/users")
    async def add_user(request: Request) -> dict[str, Any]:
        body = await request.json()
        name = body["username"]
        user = User(
            username=name,
            name=body.get("name", name.title()),
            email=body.get("email", f"{name}@example.test"),
            groups=list(body.get("groups", [])),
            blocked=bool(body.get("blocked", False)),
            email_verified=bool(body.get("email_verified", True)),
            roles=dict(body.get("roles", {})),
        )
        return user_json(state.put_user(user))

    @app.patch("/_fake/users/{username}", response_model=None)
    async def patch_user(username: str, request: Request) -> Response:
        body = await request.json()
        with state.lock:
            user = state.users.get(username)
            if user is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            for key in ("name", "email", "groups", "blocked", "email_verified", "roles", "auth_time"):
                if key in body:
                    setattr(user, key, body[key])
            return JSONResponse(user_json(user))

    @app.delete("/_fake/users/{username}")
    def delete_user(username: str) -> dict[str, bool]:
        with state.lock:
            return {"deleted": state.users.pop(username, None) is not None}

    @app.post("/_fake/current-user")
    async def current_user(request: Request) -> dict[str, Any]:
        body = await request.json()
        with state.lock:
            state.current_user = body.get("username")
            if state.current_user in state.users and "auth_age_s" in body:
                state.users[state.current_user].auth_time = int(time.time() - float(body["auth_age_s"]))
        return {"current_user": state.current_user}

    @app.post("/_fake/approve-device", response_model=None)
    async def approve_device(request: Request) -> Response:
        body = await request.json()
        with state.lock:
            for device in state.devices.values():
                if device["user_code"] == body.get("user_code"):
                    if body.get("deny"):
                        device["status"] = "denied"
                    else:
                        if body.get("username") not in state.users:
                            return JSONResponse({"error": "unknown user"}, status_code=404)
                        device["status"], device["user"] = "approved", body["username"]
                    return JSONResponse({"status": device["status"]})
        return JSONResponse({"error": "unknown user_code"}, status_code=404)

    @app.post("/_fake/faults", response_model=None)
    async def add_fault(request: Request) -> Response:
        body = await request.json()
        names = body.get("faults") or [body.get("fault")]
        if any(n not in FAULTS for n in names):
            return JSONResponse({"error": f"fault must be one of {FAULTS}"}, status_code=422)
        with state.lock:
            if body.get("sticky"):
                state.sticky_faults.update(names)
            else:
                state.faults.extend(names)
            return JSONResponse({"next": state.faults, "sticky": sorted(state.sticky_faults)})

    @app.delete("/_fake/faults")
    def clear_faults() -> dict[str, bool]:
        with state.lock:
            state.faults.clear()
            state.sticky_faults.clear()
        return {"ok": True}

    @app.post("/_fake/clients")
    async def add_client(request: Request) -> dict[str, Any]:
        body = await request.json()
        client = Client(body["client_id"], body.get("client_secret"), list(body.get("redirect_uris", [])))
        with state.lock:
            state.clients[client.client_id] = client
        return client.__dict__

    @app.post("/_fake/config")
    async def set_config(request: Request) -> dict[str, Any]:
        body = await request.json()
        with state.lock:
            if "issuer" in body:
                state.issuer = body.pop("issuer")
            state.config.update({k: int(v) for k, v in body.items() if k in state.config})
            return {"issuer": state.issuer, **state.config}

    @app.get("/_fake/events")
    def events() -> list[dict[str, Any]]:
        with state.lock:
            return list(state.events)

    @app.post("/_fake/reset")
    def reset() -> dict[str, bool]:
        state.reset()
        return {"ok": True}

    return app
