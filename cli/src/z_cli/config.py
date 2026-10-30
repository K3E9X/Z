"""`~/.config/z/config.toml`: server profiles and preferences (docs/10 §4).

    url = "https://z.example"          # the "default" profile lives at the top level
    team = "Cyber Ops"
    theme = "night"
    autonomy_default = "propose"
    editor = "vim"
    profile = "staging"                # the active profile (optional)

    [profiles.staging]
    url = "https://z-staging.example"

Environment overrides: `Z_URL`, `Z_TOKEN`, `Z_PROFILE`; `Z_CONFIG_DIR` moves the whole directory.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from z_cli.errors import UsageError

KEYS: dict[str, str] = {
    "url": "Z server URL, e.g. https://z.example",
    "team": "default team for missions started from this machine",
    "theme": "night | day | slate | contrast",
    "autonomy_default": "propose | read_only",
    "editor": "editor for `z review edit` (default: $VISUAL, $EDITOR, then vi)",
}
THEMES = ("night", "day", "slate", "contrast")
AUTONOMY = ("propose", "read_only")
DEFAULT_PROFILE = "default"


def config_dir() -> Path:
    if os.environ.get("Z_CONFIG_DIR"):
        return Path(os.environ["Z_CONFIG_DIR"]).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "z"


def state_dir() -> Path:
    if os.environ.get("Z_CONFIG_DIR"):
        return Path(os.environ["Z_CONFIG_DIR"]).expanduser() / "state"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base).expanduser() / "z"


def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith("/api/v1"):
        url = url[: -len("/api/v1")]
    if not url.startswith(("http://", "https://")):
        raise UsageError(f"not a URL: {url!r}", hint="Use the full address, e.g. https://z.example")
    return url


def validate(key: str, value: str) -> str:
    if key not in KEYS:
        raise UsageError(f"unknown config key {key!r}", hint="Keys: " + ", ".join(KEYS))
    if key == "url":
        return normalize_url(value)
    if key == "theme" and value not in THEMES:
        raise UsageError(f"theme must be one of {', '.join(THEMES)}")
    if key == "autonomy_default" and value not in AUTONOMY:
        raise UsageError(f"autonomy_default must be one of {', '.join(AUTONOMY)}")
    return value


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'


def _bare_key(key: str) -> str:
    if key and all(c.isalnum() or c in "-_" for c in key):
        return key
    return _quote(key)


@dataclass
class Config:
    """Profiles of string settings; the top-level table is the `default` profile."""

    path: Path
    profiles: dict[str, dict[str, str]] = field(default_factory=lambda: {DEFAULT_PROFILE: {}})
    active: str | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or config_dir() / "config.toml"
        if not path.is_file():
            return cls(path)
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise UsageError(f"cannot read {path}: {exc}") from exc
        profiles: dict[str, dict[str, str]] = {DEFAULT_PROFILE: {}}
        for key, value in data.items():
            if key == "profiles" and isinstance(value, dict):
                for name, table in value.items():
                    if isinstance(table, dict):
                        profiles[name] = {k: str(v) for k, v in table.items() if k in KEYS}
            elif key in KEYS:
                profiles[DEFAULT_PROFILE][key] = str(value)
        active = data.get("profile")
        return cls(path, profiles, str(active) if active else None)

    def save(self) -> None:
        lines = ["# z CLI configuration (docs/10 §4). Edit with `z config set <key> <value>`."]
        if self.active and self.active != DEFAULT_PROFILE:
            lines.append(f"profile = {_quote(self.active)}")
        for key, value in sorted(self.profiles.get(DEFAULT_PROFILE, {}).items()):
            lines.append(f"{key} = {_quote(value)}")
        for name in sorted(p for p in self.profiles if p != DEFAULT_PROFILE):
            lines += ["", f"[profiles.{_bare_key(name)}]"]
            lines += [f"{k} = {_quote(v)}" for k, v in sorted(self.profiles[name].items())]
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    # ---------------------------------------------------------------- profiles

    def profile_name(self, override: str | None = None) -> str:
        name = override or os.environ.get("Z_PROFILE") or self.active or DEFAULT_PROFILE
        if name not in self.profiles and name != DEFAULT_PROFILE:
            raise UsageError(
                f"no profile named {name!r}", hint="List them with `z config profiles`; add one with `z config add`."
            )
        return name

    def profile(self, override: str | None = None) -> dict[str, str]:
        return self.profiles.setdefault(self.profile_name(override), {})

    def get(self, key: str, profile: str | None = None) -> str | None:
        if key == "url" and os.environ.get("Z_URL"):
            return normalize_url(os.environ["Z_URL"])
        return self.profile(profile).get(key)

    def set(self, key: str, value: str, profile: str | None = None) -> str:
        checked = validate(key, value)
        self.profile(profile)[key] = checked
        return checked

    def unset(self, key: str, profile: str | None = None) -> bool:
        if key not in KEYS:
            raise UsageError(f"unknown config key {key!r}", hint="Keys: " + ", ".join(KEYS))
        return self.profile(profile).pop(key, None) is not None

    def add_profile(self, name: str, url: str) -> None:
        if not name or not all(c.isalnum() or c in "-_." for c in name):
            raise UsageError("profile names use letters, digits, '-', '_' and '.'")
        self.profiles.setdefault(name, {})["url"] = normalize_url(url)

    def remove_profile(self, name: str) -> None:
        if name == DEFAULT_PROFILE:
            raise UsageError("the default profile cannot be removed")
        if self.profiles.pop(name, None) is None:
            raise UsageError(f"no profile named {name!r}")
        if self.active == name:
            self.active = None

    def use(self, name: str) -> None:
        if name not in self.profiles and name != DEFAULT_PROFILE:
            raise UsageError(f"no profile named {name!r}")
        self.active = None if name == DEFAULT_PROFILE else name

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "active": self.profile_name(),
            "profiles": {name: dict(values) for name, values in sorted(self.profiles.items())},
        }
