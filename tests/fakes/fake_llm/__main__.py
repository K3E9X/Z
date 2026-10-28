"""`uv run python -m tests.fakes.fake_llm --host 0.0.0.0 --port 8090 [--rules rules.yaml]`."""

import argparse

from .._server import run_cli
from .app import create_app


def _args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rules", default=None, help="YAML rules file (default: $FAKE_LLM_RULES)")
    parser.add_argument("--embed-dim", type=int, default=None, help="default: $FAKE_LLM_EMBED_DIM or 1024")


run_cli("Fake OpenAI-compatible LLM", 8090, lambda a: create_app(a.rules, a.embed_dim), _args)
