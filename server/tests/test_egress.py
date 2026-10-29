"""Runners reach package mirrors only through the egress proxy; Z and LiteLLM stay direct."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.server import base_config


def test_runner_env_uses_the_egress_proxy_only_when_configured(
    make_client: Callable[..., TestClient], oidc: FakeGitLabOIDC
) -> None:
    from z_server.db.models import Job
    from z_server.scheduler.queue import runner_env

    plain = make_client()
    env = runner_env(plain.app.state.z, Job(id="job_1"), "zb_x")  # type: ignore[attr-defined]
    assert "HTTPS_PROXY" not in env and env["Z_JOB_TOKEN"] == "zb_x"
    config = base_config(oidc.issuer)
    config["egress"]["proxy_url"] = "http://egress-proxy:3128"
    proxied = make_client(config=config, internal_url="http://z-server:8000", litellm_url="http://litellm:4000")
    env = runner_env(proxied.app.state.z, Job(id="job_2"), "zb_y")  # type: ignore[attr-defined]
    assert env["HTTPS_PROXY"] == env["http_proxy"] == "http://egress-proxy:3128"
    assert set(env["NO_PROXY"].split(",")) == {"z-server", "litellm"}
