"""GitLab as an OpenID Connect provider (docs/12 §1, ADR 0010).

Authorization code + PKCE (S256) with state and nonce; the ID token is validated for signature (JWKS),
issuer, audience, expiry, nonce and, for step-up, auth_time. Roles come from userinfo `groups` paths.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWK

LEEWAY_S = 60
ALLOWED_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384")


class OIDCError(Exception):
    """Sign-in refused. `reason` is a stable code used in audit events and the sign-in page."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    jwks_uri: str
    revocation_endpoint: str | None = None
    device_authorization_endpoint: str | None = None


class OIDCClient:
    def __init__(self, issuer: str, client_id: str, client_secret: str, http: httpx.AsyncClient) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = http
        self._discovery: Discovery | None = None
        self._jwks: dict[str, PyJWK] = {}
        self._jwks_fetched = 0.0

    async def discovery(self) -> Discovery:
        if self._discovery is None:
            try:
                resp = await self.http.get(f"{self.issuer}/.well-known/openid-configuration", timeout=10)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise OIDCError("provider_unreachable", str(exc)) from exc
            if data.get("issuer", "").rstrip("/") != self.issuer:
                raise OIDCError("bad_discovery", f"issuer {data.get('issuer')!r} does not match {self.issuer!r}")
            self._discovery = Discovery(
                issuer=data["issuer"],
                authorization_endpoint=data["authorization_endpoint"],
                token_endpoint=data["token_endpoint"],
                userinfo_endpoint=data["userinfo_endpoint"],
                jwks_uri=data["jwks_uri"],
                revocation_endpoint=data.get("revocation_endpoint"),
                device_authorization_endpoint=data.get("device_authorization_endpoint"),
            )
        return self._discovery

    async def _keys(self, force: bool = False) -> dict[str, PyJWK]:
        # Refetch at most once a minute when an unknown kid shows up (key rotation).
        if self._jwks and not (force and time.monotonic() - self._jwks_fetched > 60):
            return self._jwks
        disc = await self.discovery()
        try:
            resp = await self.http.get(disc.jwks_uri, timeout=10)
            resp.raise_for_status()
            keys = resp.json().get("keys", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("jwks_unreachable", str(exc)) from exc
        self._jwks = {k.get("kid", ""): PyJWK.from_dict(k) for k in keys if k.get("use", "sig") == "sig"}
        self._jwks_fetched = time.monotonic()
        return self._jwks

    async def jwks_status(self) -> tuple[bool, int, str | None]:
        try:
            keys = await self._keys(force=True)
        except OIDCError as exc:
            return False, 0, exc.reason
        return bool(keys), len(keys), None

    async def authorize_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
        scopes: list[str],
        *,
        step_up_max_age: int | None = None,
    ) -> str:
        disc = await self.discovery()
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if step_up_max_age is not None:
            params["prompt"] = "login"
            params["max_age"] = str(step_up_max_age)
        return f"{disc.authorization_endpoint}?{urlencode(params)}"

    async def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        disc = await self.discovery()
        try:
            resp = await self.http.post(
                disc.token_endpoint, data=form, auth=(self.client_id, self.client_secret), timeout=15
            )
        except httpx.HTTPError as exc:
            raise OIDCError("provider_unreachable", str(exc)) from exc
        if resp.status_code != 200:
            error = _error_code(resp)
            raise OIDCError("token_refused", error)
        data: dict[str, Any] = resp.json()
        return data

    async def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> dict[str, Any]:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    async def validate_id_token(
        self, id_token: str, nonce: str | None, *, min_auth_time: float | None = None
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OIDCError("bad_token", str(exc)) from exc
        if header.get("alg") not in ALLOWED_ALGS:
            raise OIDCError("bad_token", f"algorithm {header.get('alg')!r} not allowed")
        kid = header.get("kid", "")
        keys = await self._keys()
        if kid not in keys:
            keys = await self._keys(force=True)
        if kid not in keys:
            raise OIDCError("bad_signature", "unknown signing key")
        disc = await self.discovery()
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=keys[kid],
                algorithms=list(ALLOWED_ALGS),
                audience=self.client_id,
                issuer=disc.issuer,
                leeway=LEEWAY_S,
                options={"require": ["iss", "sub", "aud", "exp", "iat"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise OIDCError("expired_token", str(exc)) from exc
        except jwt.InvalidAudienceError as exc:
            raise OIDCError("bad_audience", str(exc)) from exc
        except jwt.InvalidIssuerError as exc:
            raise OIDCError("bad_issuer", str(exc)) from exc
        except jwt.InvalidSignatureError as exc:
            raise OIDCError("bad_signature", str(exc)) from exc
        except jwt.PyJWTError as exc:
            raise OIDCError("bad_token", str(exc)) from exc
        if nonce is not None and claims.get("nonce") != nonce:
            raise OIDCError("bad_nonce", "nonce does not match the request")
        if min_auth_time is not None and float(claims.get("auth_time", 0)) + LEEWAY_S < min_auth_time:
            raise OIDCError("stale_authentication", "the provider did not re-authenticate the user")
        return claims

    async def userinfo(self, access_token: str) -> dict[str, Any]:
        disc = await self.discovery()
        try:
            resp = await self.http.get(
                disc.userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"}, timeout=10
            )
        except httpx.HTTPError as exc:
            raise OIDCError("provider_unreachable", str(exc)) from exc
        if resp.status_code != 200:
            raise OIDCError("userinfo_refused", str(resp.status_code))
        data: dict[str, Any] = resp.json()
        return data


def _error_code(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error", resp.status_code))
    except ValueError:
        return str(resp.status_code)
