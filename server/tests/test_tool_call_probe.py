"""Synthetic tool-call probe per alias, through the LiteLLM gateway only, shown in GET /inference/health (ADR 0034)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from z_server import probes
from z_server.llm import inference

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.fakes.fake_llm import FakeLLM
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.server import base_config, seed_packs, sign_in_as

GOOD = {"tool_calls": [{"name": "read_file", "arguments": {"path": "src/app.py"}}]}


@pytest.fixture(scope="module")
def llm() -> Iterator[FakeLLM]:
    with FakeLLM() as fake:
        yield fake


@pytest.fixture
def keys() -> RecordingKeyAdmin:
    return RecordingKeyAdmin()


@pytest.fixture
def app(
    make_client: Callable[..., TestClient], llm: FakeLLM, keys: RecordingKeyAdmin, oidc: FakeGitLabOIDC, tmp_path: Any
) -> TestClient:
    llm.reset()
    config = base_config(oidc.issuer, **{"inference.probes": {"models_poll_s": 60, "tool_call_probe_s": 300}})
    client = make_client(config=config, llm_keys=keys, litellm_url=llm.url, artifacts_dir=tmp_path / "a")
    seed_packs(client)
    return client


def _tick(app: TestClient, at: datetime) -> dict[str, bool | None]:
    state = app.app.state.z  # type: ignore[attr-defined]
    return dict(app.portal.call(probes.tick, state, at))  # type: ignore[union-attr]


def _models(app: TestClient, oidc: FakeGitLabOIDC) -> dict[str, dict[str, Any]]:
    inference._cache.clear()
    sign_in_as(app, oidc, "victor")
    resp = app.get("/api/v1/inference/health")
    assert resp.status_code == 200, resp.text
    return {m["alias"]: m for m in resp.json()["models"]}


def test_probe_outcomes_become_the_parse_error_rate(
    app: TestClient, llm: FakeLLM, keys: RecordingKeyAdmin, oidc: FakeGitLabOIDC
) -> None:
    before = _models(app, oidc)
    assert all(m.get("tool_call_parse_error_rate") is None for m in before.values())
    llm.add_rules(
        [
            {"match": {"model": "z-fast", "has_tools": True}, "responses": [GOOD], "repeat": True},
            {  # malformed arguments
                "id": "heavy",
                "match": {"model": "z-heavy", "has_tools": True},
                "responses": [{"tool_calls": [{"name": "read_file", "arguments": '{"path": "src/app.py"'}]}],
                "repeat": True,
            },
            {"match": {"model": "z-judge"}, "responses": [{"text": "I read it for you."}], "repeat": True},
        ]
    )
    start = datetime.now(UTC)
    assert _tick(app, start) == {"z-fast": True, "z-heavy": False, "z-judge": False}
    sent = llm.requests()
    assert sorted(r["model"] for r in sent) == ["z-fast", "z-heavy", "z-judge"]  # never z-embed
    platform = next(c for c in keys.calls if c[0] == "create" and c[1]["alias"] == "z-platform")
    assert set(platform[1]["models"]) >= {"z-fast", "z-heavy", "z-judge"}  # the internal-only platform key
    for request in sent:
        assert request["headers"]["authorization"].startswith("Bearer sk-test-")
        assert request["body"]["tools"][0]["function"]["name"] == "read_file"

    assert _tick(app, start + timedelta(seconds=60)) == {}  # not due before tool_call_probe_s
    llm.add_rules(
        {"id": "heavy", "match": {"model": "z-heavy", "has_tools": True}, "responses": [GOOD], "repeat": True}
    )
    llm.add_rules(  # a gateway error says nothing about parsing: not counted
        {"match": {"model": "z-judge"}, "responses": [{"error": {"status": 503, "message": "busy"}}], "repeat": True}
    )
    assert _tick(app, start + timedelta(seconds=301)) == {"z-fast": True, "z-heavy": True, "z-judge": None}

    models = _models(app, oidc)
    assert models["z-fast"]["tool_call_ok"] is True and models["z-fast"]["tool_call_parse_error_rate"] == 0.0
    assert models["z-heavy"]["tool_call_ok"] is True and models["z-heavy"]["tool_call_parse_error_rate"] == 0.5
    assert models["z-judge"]["tool_call_ok"] is False and models["z-judge"]["tool_call_parse_error_rate"] == 1.0
    assert models["z-heavy"]["last_probe_at"]
    assert models["z-embed"].get("tool_call_parse_error_rate") is None


def test_the_rate_keeps_the_last_probes_only(app: TestClient, llm: FakeLLM, oidc: FakeGitLabOIDC) -> None:
    llm.add_rules({"match": {"has_tools": True}, "responses": [{"text": "no tools today"}], "repeat": True})
    start = datetime.now(UTC)
    _tick(app, start)
    llm.add_rules({"match": {"has_tools": True}, "responses": [GOOD], "repeat": True})
    for i in range(1, probes.WINDOW + 1):
        _tick(app, start + timedelta(seconds=301 * i))
    models = _models(app, oidc)
    assert models["z-fast"]["tool_call_parse_error_rate"] == 0.0  # the first failure fell out of the window
