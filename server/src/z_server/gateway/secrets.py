"""Secrets backends (docs/05 §4). The database and manifests hold references; values live only in memory here.

Reference formats (common.schema.json SecretRef):
- `env:NAME`                         environment variable (development, compose)
- `file:/run/secrets/name`           mounted file (Docker/Kubernetes secrets), trailing newline stripped
- `kubernetes:<namespace>/<name>#<key>`  Secret read through the in-cluster API with the pod's service account
- `vault:<mount>/<path>#<key>`       HashiCorp Vault KV v2 (VAULT_ADDR + VAULT_TOKEN, or Kubernetes auth with
                                     VAULT_K8S_ROLE); `vault:<mount>/<path>` without key returns the JSON object
Values are cached for a short TTL so rotation is picked up without restarts.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
CACHE_TTL_S = 60.0


class SecretError(RuntimeError):
    """A reference could not be resolved. The message never contains a value."""


@dataclass
class SecretResolver:
    http: httpx.AsyncClient
    env: dict[str, str] | None = None
    ttl_s: float = CACHE_TTL_S
    _cache: dict[str, tuple[float, str]] = field(default_factory=dict)
    _vault_token: str | None = None
    _k8s: httpx.AsyncClient | None = None

    def _environ(self) -> dict[str, str]:
        return self.env if self.env is not None else dict(os.environ)

    async def resolve(self, ref: str) -> str:
        cached = self._cache.get(ref)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        backend, _, rest = ref.partition(":")
        if not rest:
            raise SecretError(f"malformed secret reference {backend!r}")
        if backend == "env":
            value = self._environ().get(rest)
            if not value:
                raise SecretError(f"environment variable {rest} is not set")
        elif backend == "file":
            path = Path(rest)
            if not path.is_absolute():
                raise SecretError("file secret references must be absolute paths")
            try:
                value = path.read_text().rstrip("\r\n")
            except OSError as exc:
                raise SecretError(f"cannot read secret file {rest}: {exc.strerror}") from exc
        elif backend == "kubernetes":
            value = await self._kubernetes(rest)
        elif backend == "vault":
            value = await self._vault(rest)
        else:
            raise SecretError(f"unknown secrets backend {backend!r}")
        self._cache[ref] = (time.monotonic() + self.ttl_s, value)
        return value

    async def aclose(self) -> None:
        if self._k8s is not None:
            await self._k8s.aclose()

    def forget(self, ref: str | None = None) -> None:
        if ref is None:
            self._cache.clear()
        else:
            self._cache.pop(ref, None)

    # ---------------------------------------------------------------- kubernetes

    async def _kubernetes(self, rest: str) -> str:
        location, _, key = rest.partition("#")
        namespace, _, name = location.partition("/")
        if not (namespace and name and key):
            raise SecretError("kubernetes references are kubernetes:<namespace>/<name>#<key>")
        env = self._environ()
        host, port = env.get("KUBERNETES_SERVICE_HOST"), env.get("KUBERNETES_SERVICE_PORT", "443")
        token_file = SA_DIR / "token"
        if not host or not token_file.exists():
            raise SecretError("not running in a Kubernetes pod (no service account)")
        if self._k8s is None:  # the API server certificate is signed by the cluster CA, not a public one
            self._k8s = httpx.AsyncClient(verify=str(SA_DIR / "ca.crt"), timeout=10)
        resp = await self._k8s.get(
            f"https://{host}:{port}/api/v1/namespaces/{namespace}/secrets/{name}",
            headers={"Authorization": f"Bearer {token_file.read_text().strip()}"},
        )
        if resp.status_code != 200:
            raise SecretError(f"Kubernetes API answered {resp.status_code} for secret {namespace}/{name}")
        data = resp.json().get("data") or {}
        if key not in data:
            raise SecretError(f"secret {namespace}/{name} has no key {key}")
        return base64.b64decode(data[key]).decode()

    # ---------------------------------------------------------------- vault

    async def _vault_auth(self, addr: str) -> str:
        env = self._environ()
        if env.get("VAULT_TOKEN"):
            return env["VAULT_TOKEN"]
        if self._vault_token:
            return self._vault_token
        role = env.get("VAULT_K8S_ROLE")
        token_file = SA_DIR / "token"
        if not role or not token_file.exists():
            raise SecretError("Vault needs VAULT_TOKEN or VAULT_K8S_ROLE inside Kubernetes")
        mount = env.get("VAULT_K8S_MOUNT", "kubernetes")
        resp = await self.http.post(
            f"{addr}/v1/auth/{mount}/login", json={"role": role, "jwt": token_file.read_text().strip()}
        )
        if resp.status_code != 200:
            raise SecretError(f"Vault Kubernetes login answered {resp.status_code}")
        self._vault_token = str(resp.json()["auth"]["client_token"])
        return self._vault_token

    async def _vault(self, rest: str) -> str:
        env = self._environ()
        addr = env.get("VAULT_ADDR", "").rstrip("/")
        if not addr:
            raise SecretError("VAULT_ADDR is not set")
        location, _, key = rest.partition("#")
        mount, _, path = location.partition("/")
        if not (mount and path):
            raise SecretError("vault references are vault:<mount>/<path>[#key]")
        headers = {"X-Vault-Token": await self._vault_auth(addr)}
        if env.get("VAULT_NAMESPACE"):
            headers["X-Vault-Namespace"] = env["VAULT_NAMESPACE"]
        resp = await self.http.get(f"{addr}/v1/{mount}/data/{path}", headers=headers)
        if resp.status_code == 403 and self._vault_token:
            self._vault_token = None  # expired login: retry once
            headers["X-Vault-Token"] = await self._vault_auth(addr)
            resp = await self.http.get(f"{addr}/v1/{mount}/data/{path}", headers=headers)
        if resp.status_code != 200:
            raise SecretError(f"Vault answered {resp.status_code} for {mount}/{path}")
        data: dict[str, Any] = (resp.json().get("data") or {}).get("data") or {}
        if not key:
            return json.dumps(data)
        if key not in data:
            raise SecretError(f"Vault secret {mount}/{path} has no key {key}")
        return str(data[key])
