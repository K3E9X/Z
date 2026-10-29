"""Process settings from the environment (docs: .env.example). Platform policy lives in config/z.yaml."""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from z_common.config import load_config


class SettingsError(RuntimeError):
    """The server refuses to start with an unsafe or incomplete configuration."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    env: str = "development"
    secret_key: str = ""
    public_url: str = "http://localhost:8080"
    config_path: Path = Path("config/z.yaml")
    packs_path: Path = Path("packs")
    artifacts_dir: Path = Path("data/artifacts")
    litellm_url: str = "http://litellm:4000"
    litellm_master_key: str = ""
    internal_url: str = "http://z-server:8000"  # how runners reach z-server (internal API and gateway)
    runner_litellm_url: str = ""  # how runners reach LiteLLM; defaults to litellm_url
    executor: str = "docker"
    # image the docker and kubernetes executors start; pinned by digest in production
    runner_image: str = "z-runner:local"
    breakglass: bool = False
    test_account_passwords: str = ""
    oidc_client_secret: str = ""
    browser_url: str = ""  # the z-browser worker (browser connectors); empty: not deployed
    browser_token: str = ""
    config: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def production(self) -> bool:
        return self.env == "production"

    @property
    def auth_mode(self) -> str:
        return str(self.config.get("auth", {}).get("mode", "oidc"))

    def check(self) -> None:
        """Refuse dangerous combinations (docs/07 §4, docs/12 §1)."""
        problems = []
        if len(self.secret_key) < 32:
            problems.append("Z_SECRET_KEY must be at least 32 characters")
        if self.browser_url and len(self.browser_token) < 16:
            problems.append("Z_BROWSER_TOKEN must be set (at least 16 characters) with Z_BROWSER_URL")
        if self.production:
            if self.auth_mode == "dev":
                problems.append("auth.mode dev is refused when Z_ENV=production")
            if self.executor == "process":
                problems.append("the process executor is refused when Z_ENV=production")
            if self.config.get("auth", {}).get("local_accounts", {}).get("test_accounts"):
                problems.append("test accounts are refused when Z_ENV=production")
            if self.executor in {"docker", "kubernetes"} and "@sha256:" not in self.runner_image:
                problems.append("Z_RUNNER_IMAGE must be pinned by digest (image@sha256:...) when Z_ENV=production")
            if self.secret_key.startswith("change-me"):
                problems.append("Z_SECRET_KEY still has the example value")
        if problems:
            raise SettingsError("; ".join(problems))


def _database_url(raw: str) -> str:
    # Accept postgresql:// and postgresql+psycopg://; the async engine needs the psycopg driver.
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg://" + raw.removeprefix("postgresql://")
    return raw


def from_env(env: dict[str, str] | None = None) -> Settings:
    e = dict(os.environ if env is None else env)
    config_path = Path(e.get("Z_CONFIG", "config/z.yaml"))
    config = load_config(config_path, e) if config_path.is_file() else {}
    if not e.get("DATABASE_URL"):
        raise SettingsError("DATABASE_URL is required")
    return Settings(
        database_url=_database_url(e["DATABASE_URL"]),
        env=e.get("Z_ENV", "development"),
        secret_key=e.get("Z_SECRET_KEY", ""),
        public_url=e.get("Z_PUBLIC_URL", "http://localhost:8080").rstrip("/"),
        config_path=config_path,
        packs_path=Path(e.get("Z_PACKS", "packs")),
        artifacts_dir=Path(e.get("Z_ARTIFACTS_DIR", "data/artifacts")),
        litellm_url=e.get("LITELLM_URL", "http://litellm:4000").rstrip("/"),
        litellm_master_key=e.get("LITELLM_MASTER_KEY", ""),
        internal_url=e.get("Z_INTERNAL_URL", "http://z-server:8000").rstrip("/"),
        runner_litellm_url=e.get("Z_RUNNER_LITELLM_URL", "").rstrip("/"),
        executor=e.get("Z_EXECUTOR", str(config.get("executor", {}).get("kind", "docker"))),
        runner_image=e.get("Z_RUNNER_IMAGE", "z-runner:local"),
        breakglass=e.get("Z_BREAKGLASS", "false").lower() == "true",
        test_account_passwords=e.get("Z_TEST_ACCOUNT_PASSWORDS", ""),
        oidc_client_secret=e.get("OIDC_CLIENT_SECRET", ""),
        browser_url=e.get("Z_BROWSER_URL", "").rstrip("/"),
        browser_token=e.get("Z_BROWSER_TOKEN", ""),
        config=config,
    )


@functools.cache
def get_settings() -> Settings:
    return from_env()
