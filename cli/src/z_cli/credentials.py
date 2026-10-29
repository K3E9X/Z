"""Where the Z CLI token lives: the OS keyring when one is usable, else `~/.config/z/token` (mode 0600).

The token is a Z session token (`zs_...`, 30 days, bound to the device name) or a personal API token
(`zt_...`); GitLab tokens are never stored (docs/12 §1). `Z_TOKEN` overrides both stores. `Z_KEYRING=off`
forces the file store (headless machines, CI).
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from z_cli.config import config_dir

SERVICE = "z-cli"
Store = Literal["env", "keyring", "file"]


@dataclass(frozen=True)
class StoredToken:
    token: str
    store: Store


def _keyring() -> Any | None:
    """The keyring module if a real backend is available (not the fail or null backends)."""
    if os.environ.get("Z_KEYRING", "").lower() in {"0", "off", "false", "no", "file"}:
        return None
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:  # pragma: no cover - keyring is a dependency
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001 - any backend error means "no keyring"
        return None
    if isinstance(backend, fail.Keyring) or type(backend).__module__.startswith("keyring.backends.null"):
        return None
    if getattr(backend, "priority", 1) <= 0:
        return None
    return keyring


def token_file() -> Path:
    return config_dir() / "token"


def _read_file() -> dict[str, Any]:
    path = token_file()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_file(data: dict[str, Any]) -> None:
    path = token_file()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def load(url: str) -> StoredToken | None:
    if os.environ.get("Z_TOKEN"):
        return StoredToken(os.environ["Z_TOKEN"], "env")
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(SERVICE, url)
        except Exception:  # noqa: BLE001 - a locked or broken keyring falls back to the file
            value = None
        if value:
            return StoredToken(value, "keyring")
    entry = _read_file().get(url)
    if isinstance(entry, dict) and isinstance(entry.get("token"), str):
        return StoredToken(entry["token"], "file")
    return None


def save(url: str, token: str) -> Store:
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE, url, token)
            stored = True
        except Exception:  # noqa: BLE001 - fall back to the file store
            stored = False
        if stored:
            _forget_file(url)
            return "keyring"
    data = _read_file()
    data[url] = {"token": token}
    _write_file(data)
    return "file"


def _forget_file(url: str) -> bool:
    data = _read_file()
    if url not in data:
        return False
    del data[url]
    if data:
        _write_file(data)
    else:
        with contextlib.suppress(OSError):
            token_file().unlink()
    return True


def forget(url: str) -> bool:
    removed = False
    kr = _keyring()
    if kr is not None:
        try:
            if kr.get_password(SERVICE, url):
                kr.delete_password(SERVICE, url)
                removed = True
        except Exception:  # noqa: BLE001 - nothing to remove from a broken keyring
            removed = False
    return _forget_file(url) or removed
