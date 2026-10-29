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
        reveal(
            "Z_BREAKGLASS_SECRET_FILE",
            "break-glass account enabled",
            f"break-glass: one-time password {breakglass.password} TOTP {breakglass.totp_uri} (shown once)\n",
        )


def reveal(file_env: str, what: str, secret_text: str) -> None:
    """A one-time secret for the operator. Kubernetes: written to the file named by `file_env` (a memory-backed
    volume read with kubectl exec), never to the logs a log pipeline ships. Compose: printed once on stdout, read
    with `docker compose logs` on the host."""
    target = os.environ.get(file_env)
    if target:
        path = Path(target)
        path.write_text(secret_text)
        path.chmod(0o600)
        log.warning("%s; secret written to %s (read it once, then delete the file)", what, target)
    else:
        print(secret_text.strip(), flush=True)


def announce_bootstrap(secret: local.CreatedSecret) -> None:
    reveal(
        "Z_BOOTSTRAP_SECRET_FILE",
        f"bootstrap account {secret.name} created (valid 24 h)",
        f"bootstrap: user {secret.name} one-time password {secret.password} (valid 24 h, shown once)\n",
    )
