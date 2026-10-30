"""Static checks on a connector's OAuth settings (docs/05 §6), shared by the pack linter and the connector API."""

from __future__ import annotations

from typing import Any

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (an endpoint, not a secret)


def provider(manifest: dict[str, Any]) -> str | None:
    cfg = manifest.get("oauth") or {}
    if cfg.get("provider"):
        return str(cfg["provider"])
    return "microsoft" if cfg.get("tenant_id") else None


def connector_problems(manifest: dict[str, Any]) -> list[str]:
    if provider(manifest) != "google":
        return []
    problems = []
    for purpose, auth in (manifest.get("auth") or {}).items():
        service = auth.get("service") if isinstance(auth, dict) and "type" not in auth else None
        if isinstance(service, dict) and service.get("flow") == "client_credentials":
            problems.append(
                f"auth.{purpose}.service: Google has no client credentials grant; use delegated (user) connections"
            )
    return problems
