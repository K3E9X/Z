"""`z-server` command line: api | all | migrate | seed | bootstrap | check-config."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ALEMBIC_INI = Path(__file__).parent / "db" / "alembic.ini"


def _migrate() -> int:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ALEMBIC_INI)), "head")
    print("database migrated", flush=True)
    return 0


def _serve(host: str, port: int, workers: bool) -> int:
    import uvicorn

    from z_server.app import create_app
    from z_server.settings import get_settings

    uvicorn.run(
        create_app(get_settings(), workers=workers),
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("Z_TRUSTED_PROXIES", "127.0.0.1"),
        log_level="info",
        # open SSE streams would hold a graceful shutdown forever; clients resume with Last-Event-ID
        timeout_graceful_shutdown=int(os.environ.get("Z_SHUTDOWN_GRACE_S", "10")),
    )
    return 0


def _check_config() -> int:
    from z_server.settings import SettingsError, get_settings

    try:
        get_settings().check()
    except (SettingsError, Exception) as exc:  # noqa: BLE001 — print every configuration problem
        print(f"configuration error: {exc}", file=sys.stderr)
        return 1
    print("configuration ok", flush=True)
    return 0


def _bootstrap_reset(ttl_hours: int) -> int:
    from z_server import audit
    from z_server.auth import local
    from z_server.crypto import AuditSigner, Sealer
    from z_server.db.session import make_engine, make_sessionmaker
    from z_server.settings import get_settings
    from z_server.startup import announce_bootstrap

    settings = get_settings()

    async def run() -> local.CreatedSecret:
        engine = make_engine(settings.database_url, pool_size=1)
        try:
            async with make_sessionmaker(engine)() as db, db.begin():
                secret = await local.reset_bootstrap(db, ttl_hours)
                await audit.record(db, audit.Actor("system:cli"), "auth.bootstrap_reset", "local_account", secret.name)
                return secret
        finally:
            await engine.dispose()

    _ = (AuditSigner, Sealer)
    announce_bootstrap(asyncio.run(run()))
    return 0


def _seed(packs: str, only: list[str] | None) -> int:
    from z_server import audit
    from z_server.catalog.seed import SeedError, seed
    from z_server.db.session import make_engine, make_sessionmaker
    from z_server.settings import get_settings

    settings = get_settings()

    async def run() -> int:
        engine = make_engine(settings.database_url, pool_size=1)
        try:
            async with make_sessionmaker(engine)() as db, db.begin():
                report = await seed(db, Path(packs), audit.Actor("system:cli"), set(only) if only else None)
        except SeedError as exc:
            for problem in exc.problems:
                print(f"ERROR {problem}", file=sys.stderr)
            return 1
        finally:
            await engine.dispose()
        for kind in ("created", "updated", "kept", "warnings"):
            for line in getattr(report, kind):
                print(f"{kind:8} {line}")
        return 0

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("Z_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    parser = argparse.ArgumentParser(prog="z-server")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("api", "all"):
        p = sub.add_parser(name, help="serve the API" + (" and every background worker" if name == "all" else ""))
        p.add_argument("--host", default=os.environ.get("Z_HOST", "0.0.0.0"))  # noqa: S104 — container entrypoint
        p.add_argument("--port", type=int, default=int(os.environ.get("Z_PORT", "8000")))
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("check-config", help="validate environment and config/z.yaml")
    sd = sub.add_parser("seed", help="load packs into the catalog (idempotent)")
    sd.add_argument("--packs", default=os.environ.get("Z_PACKS", "packs"))
    sd.add_argument("--only", nargs="*", help="pack names to load (default: all)")
    b = sub.add_parser("bootstrap", help="manage the bootstrap admin")
    b.add_argument("--reset", action="store_true", required=True)
    b.add_argument("--ttl-hours", type=int, default=24)
    args = parser.parse_args(argv)
    if args.command in {"api", "all"}:
        return _serve(args.host, args.port, workers=args.command == "all")
    if args.command == "migrate":
        return _migrate()
    if args.command == "check-config":
        return _check_config()
    if args.command == "seed":
        return _seed(args.packs, args.only)
    if args.command == "bootstrap":
        return _bootstrap_reset(args.ttl_hours)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
