"""GitLab-like OIDC provider. See tests/fakes/README.md."""

from __future__ import annotations

from typing import Any

from .._server import FakeServer
from .app import DEVICE_GRANT, FAULTS, create_app

__all__ = ["DEVICE_GRANT", "FAULTS", "FakeGitLabOIDC", "create_app"]


class FakeGitLabOIDC(FakeServer):
    def __init__(self, issuer: str | None = None, **kwargs: Any) -> None:
        super().__init__(create_app(issuer), **kwargs)

    @property
    def issuer(self) -> str:
        with self.client() as c:
            issuer: str = c.get("/.well-known/openid-configuration").json()["issuer"]
            return issuer

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        with self.client() as c:
            r = c.request(method, path, json=body)
            r.raise_for_status()
            return r.json()

    def add_user(self, username: str, groups: list[str], **fields: Any) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", "/_fake/users", {"username": username, "groups": groups, **fields})
        return result

    def update_user(self, username: str, **fields: Any) -> dict[str, Any]:
        result: dict[str, Any] = self._call("PATCH", f"/_fake/users/{username}", fields)
        return result

    def delete_user(self, username: str) -> None:
        self._call("DELETE", f"/_fake/users/{username}")

    def set_current_user(self, username: str | None, auth_age_s: float | None = None) -> None:
        body: dict[str, Any] = {"username": username}
        if auth_age_s is not None:
            body["auth_age_s"] = auth_age_s
        self._call("POST", "/_fake/current-user", body)

    def approve_device(self, user_code: str, username: str) -> None:
        self._call("POST", "/_fake/approve-device", {"user_code": user_code, "username": username})

    def deny_device(self, user_code: str) -> None:
        self._call("POST", "/_fake/approve-device", {"user_code": user_code, "deny": True})

    def add_fault(self, fault: str, sticky: bool = False) -> None:
        self._call("POST", "/_fake/faults", {"fault": fault, "sticky": sticky})

    def clear_faults(self) -> None:
        self._call("DELETE", "/_fake/faults")

    def add_client(self, client_id: str, client_secret: str | None, redirect_uris: list[str]) -> None:
        body = {"client_id": client_id, "client_secret": client_secret, "redirect_uris": redirect_uris}
        self._call("POST", "/_fake/clients", body)

    def configure(self, **config: Any) -> dict[str, Any]:
        result: dict[str, Any] = self._call("POST", "/_fake/config", config)
        return result

    def reset(self) -> None:
        self._call("POST", "/_fake/reset")
