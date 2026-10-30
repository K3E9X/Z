"""`uv run python -m tests.fakes.fake_gitlab_oidc --host 0.0.0.0 --port 8095 [--issuer URL] [--client ID:SECRET:URI,...]`."""

import argparse

from .._server import run_cli
from .app import Client, create_app


def _args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--issuer", default=None, help="external base URL (default: $FAKE_OIDC_ISSUER or request URL)")
    parser.add_argument(
        "--client",
        action="append",
        default=[],
        help="extra client 'id:secret:uri1|uri2' (empty secret = public client); repeatable",
    )


def _build(args: argparse.Namespace) -> object:
    app = create_app(args.issuer)
    for spec in args.client:
        client_id, secret, uris = spec.split(":", 2)
        client = Client(client_id, secret or None, uris.split("|"))
        app.state.fake.base_clients.append(client)
        app.state.fake.clients[client_id] = client
    return app


run_cli("Fake GitLab OIDC provider", 8095, _build, _args)
