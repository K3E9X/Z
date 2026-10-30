"""`uv run python -m tests.fakes.fake_prometheus --host 0.0.0.0 --port 9090`."""

from .._server import run_cli
from .app import create_app

run_cli("Fake Prometheus", 9090, lambda _a: create_app())
