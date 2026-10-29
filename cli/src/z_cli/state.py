"""Per-invocation state: which server, which token, the config (set by the root callback)."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from z_cli import credentials
from z_cli.client import Api, AsyncApi
from z_cli.config import Config, normalize_url
from z_cli.errors import NotSignedIn, UsageError


@dataclass
class State:
    config: Config = field(default_factory=Config.load)
    url_override: str | None = None
    profile: str | None = None
    transport: httpx.BaseTransport | None = None  # tests only
    _api: Api | None = None

    def server_url(self) -> str:
        if self.url_override:
            return normalize_url(self.url_override)
        url = self.config.get("url", self.profile)
        if not url:
            raise UsageError(
                "No Z server configured.", hint="Run `z login --url https://z.example` (or set Z_URL) first."
            )
        return url

    def token(self) -> credentials.StoredToken | None:
        return credentials.load(self.server_url())

    def api(self, *, auth: bool = True) -> Api:
        if self._api is None:
            url = self.server_url()
            stored = self.token()
            if auth and stored is None:
                raise NotSignedIn(url)
            self._api = Api(url, stored.token if stored else None, transport=self.transport)
        return self._api

    def async_api(self) -> AsyncApi:
        url = self.server_url()
        stored = self.token()
        if stored is None:
            raise NotSignedIn(url)
        return AsyncApi(url, stored.token)

    def setting(self, key: str) -> str | None:
        return self.config.get(key, self.profile)

    def close(self) -> None:
        if self._api is not None:
            self._api.close()
            self._api = None


_state: State | None = None


def get() -> State:
    global _state
    if _state is None:
        _state = State()
    return _state


def set_state(state: State) -> State:
    global _state
    if _state is not None and _state is not state:
        _state.close()
    _state = state
    return state
