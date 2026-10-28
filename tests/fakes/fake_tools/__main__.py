"""`uv run python -m tests.fakes.fake_tools --host 0.0.0.0 --port 8091 [--seed seed.json]`."""

import argparse
import json
from pathlib import Path

from .._server import run_cli
from .app import create_app


def _args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--seed", default=None, help="JSON or YAML file replacing the default seed")


def _build(args: argparse.Namespace) -> object:
    seed = None
    if args.seed:
        text = Path(args.seed).read_text(encoding="utf-8")
        if args.seed.endswith((".yaml", ".yml")):
            import yaml

            seed = yaml.safe_load(text)
        else:
            seed = json.loads(text)
    return create_app(seed)


run_cli("Fake DefectDojo, CMDB, Logos, Jira, GitLab and Confluence", 8091, _build, _args)
