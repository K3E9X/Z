"""Idempotent preparation at process start: teams from the mapping, local accounts, bootstrap secret."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from z_server.auth import identity, local
from z_server.state import AppState

log = logging.getLogger("z.startup")


async def prepare(state: AppState) -> None:
    settings = state.settings
    async with state.sessions() as db, db.begin():
        mapping = await identity.effective_mapping(db, state.config_auth)
        await identity.ensure_teams(db, mapping)
        created = await local.ensure_bootstrap(db, state.config_auth)
        await local.ensure_test_accounts(
            db, state.config_auth, local.parse_passwords(settings.test_account_passwords), settings.production
        )
        breakglass = await local.ensure_breakglass(
            db, settings.breakglass and bool(state.config_auth.get("breakglass", {}).get("enabled")), state.sealer
        )
    if created is not None:
        announce_bootstrap(created)
    if breakglass is not None:
        log.warning("BREAK-GLASS enabled. One-time password: %s  TOTP: %s", breakglass.password, breakglass.totp_uri)


def announce_bootstrap(secret: local.CreatedSecret) -> None:
    """Compose: printed once. Kubernetes: written to Z_BOOTSTRAP_SECRET_FILE (mounted from the z-bootstrap Secret)."""
    target = os.environ.get("Z_BOOTSTRAP_SECRET_FILE")
    if target:
        path = Path(target)
        path.write_text(secret.password)
        path.chmod(0o600)
        log.warning("bootstrap account %s created; password written to %s", secret.name, target)
    else:
        print(f"bootstrap: user {secret.name} one-time password {secret.password} (valid 24 h, shown once)", flush=True)
