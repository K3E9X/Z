"""Shared state of the fake tools: seeded data, tokens, recorded writes and denials."""

from __future__ import annotations

import base64
import copy
import os
import threading
import time
from collections.abc import Callable
from typing import Any, Literal

from fastapi import HTTPException, Request

TOOLS = ("defectdojo", "cmdb", "logos", "jira", "gitlab", "confluence", "mattermost", "slack")
TokenKind = Literal["read", "write"]
Access = Literal["read", "write"]
Scheme = Literal["token", "bearer", "gitlab", "git", "basic"]


def default_tokens() -> dict[str, dict[str, str]]:
    """Read and write tokens per tool: env FAKE_<TOOL>_TOKEN_RO / _RW, defaults ro-<tool> / rw-<tool>."""
    return {
        tool: {
            "read": os.environ.get(f"FAKE_{tool.upper()}_TOKEN_RO", f"ro-{tool}"),
            "write": os.environ.get(f"FAKE_{tool.upper()}_TOKEN_RW", f"rw-{tool}"),
        }
        for tool in TOOLS
    }


def extract_token(scheme: Scheme, headers: Any) -> str | None:
    auth = headers.get("authorization") or ""
    low = auth.lower()
    if scheme == "token":
        return auth[6:].strip() if low.startswith("token ") else None
    if low.startswith("bearer "):
        return auth[7:].strip()
    if scheme == "gitlab" and headers.get("private-token"):
        return str(headers.get("private-token"))
    if scheme in {"git", "basic"} and low.startswith("basic "):
        try:
            _, _, password = base64.b64decode(auth[6:]).decode().partition(":")
        except ValueError:
            return None
        return password
    return None


class Store:
    def __init__(self, seed: dict[str, Any], tokens: dict[str, dict[str, str]] | None = None) -> None:
        self.lock = threading.RLock()
        self.tokens = tokens or default_tokens()
        self.default_seed = copy.deepcopy(seed)
        self.data: dict[str, Any] = {}
        self.writes: list[dict[str, Any]] = []
        self.denied: list[dict[str, Any]] = []
        self.on_seed: list[Callable[[dict[str, Any]], None]] = []
        self.base_url = "http://127.0.0.1"  # last external base URL seen (for self links and clone URLs)
        self._seq = 0

    # ---- data ----
    def load(self, seed: dict[str, Any] | None = None, replace_all: bool = True) -> None:
        with self.lock:
            new = copy.deepcopy(seed if seed is not None else self.default_seed)
            if replace_all:
                self.data = new
                self.writes.clear()
                self.denied.clear()
            else:
                self.data.update(new)
            for hook in self.on_seed:
                hook(new)

    def next_id(self) -> int:
        with self.lock:
            self._seq += 1
            return self._seq

    # ---- auth ----
    def kind(self, tool: str, token: str | None) -> TokenKind | None:
        if not token:
            return None
        pair = self.tokens[tool]
        if token == pair["write"]:
            return "write"
        if token == pair["read"]:
            return "read"
        return None

    def check(self, tool: str, scheme: Scheme, access: Access, headers: Any, what: str) -> TokenKind:
        """Return the token kind or raise 401 (unknown) / 403 (read token on a write)."""
        kind = self.kind(tool, extract_token(scheme, headers))
        if kind is None:
            self.deny(tool, what, "missing or unknown token")
            raise HTTPException(401, detail="Invalid token." if scheme == "token" else "401 Unauthorized")
        if access == "write" and kind != "write":
            self.deny(tool, what, "read token used for a write")
            raise HTTPException(403, detail="403 Forbidden: write token required")
        return kind

    def dep(self, tool: str, scheme: Scheme, access: Access) -> Callable[[Request], TokenKind]:
        """FastAPI dependency enforcing the token rules."""

        def _dep(request: Request) -> TokenKind:
            return self.check(tool, scheme, access, request.headers, f"{request.method} {request.url.path}")

        return _dep

    # ---- journal ----
    def record(self, tool: str, kind: str, token_kind: str, data: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "seq": self.next_id(),
            "ts": time.time(),
            "tool": tool,
            "type": kind,
            "token_kind": token_kind,
            "data": copy.deepcopy(data),
        }
        with self.lock:
            self.writes.append(entry)
        return entry

    def deny(self, tool: str, what: str, reason: str) -> None:
        with self.lock:
            self.denied.append({"ts": time.time(), "tool": tool, "what": what, "reason": reason})

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            counts: dict[str, int] = {}
            for w in self.writes:
                counts[w["type"]] = counts.get(w["type"], 0) + 1
            return {"writes": copy.deepcopy(self.writes), "denied": copy.deepcopy(self.denied), "counts": counts}


def now_iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime(ts if ts is not None else time.time()))
