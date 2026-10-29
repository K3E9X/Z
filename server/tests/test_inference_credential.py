"""The inferX key set from Z (ADR 0027): it goes to the LiteLLM gateway only, Z shows its fingerprint and the last
check, and the key is never returned, audited or echoed (not even in a check's error text)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from tests.fakes.fake_gitlab_oidc import FakeGitLabOIDC
from tests.support.llm_keys import RecordingKeyAdmin
from tests.support.server import csrf, sign_in_as, use_jar

KEY = "ixk_" + "0123456789abcdef" * 4
OTHER = "ixk_" + "fedcba9876543210" * 4


@pytest.fixture
def keys() -> RecordingKeyAdmin:
    return RecordingKeyAdmin()


@pytest.fixture
def app(make_client: Callable[..., TestClient], keys: RecordingKeyAdmin) -> TestClient:
    return make_client(llm_keys=keys)


def test_admins_set_the_key_and_only_its_fingerprint_comes_back(
    app: TestClient, oidc: FakeGitLabOIDC, keys: RecordingKeyAdmin
) -> None:
    use_jar(app, sign_in_as(app, oidc, "alice"))
    assert app.get("/api/v1/inference/credential").json() == {"source": "none"}
    keys.credentials["inferx"] = {"credential_values": {"api_key": "from-the-env"}, "credential_info": {}}
    assert app.get("/api/v1/inference/credential").json()["source"] == "env"

    saved = app.put("/api/v1/inference/credential", json={"api_key": KEY}, headers=csrf(app))
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["source"] == "ui" and out["updated_by"] == "alice"
    assert out["fingerprint"] == hashlib.sha256(KEY.encode()).hexdigest()[:12]
    assert out["check"]["ok"] is True and out["check"]["model"]
    assert KEY not in saved.text
    assert keys.credentials["inferx"]["credential_values"] == {"api_key": KEY}  # the gateway has it
    assert keys.credentials["inferx"]["credential_info"] == {"managed_by": "z", "fingerprint": out["fingerprint"]}
    assert KEY not in json.dumps(keys.calls)

    status = app.get("/api/v1/inference/credential").json()
    assert status["source"] == "ui" and status["fingerprint"] == out["fingerprint"]
    events = app.get("/api/v1/audit", params={"action": "inference.credential.replaced"}).json()["items"]
    assert len(events) == 1 and KEY not in json.dumps(events)
    assert events[0]["after"]["fingerprint"] == out["fingerprint"]

    replaced = app.put("/api/v1/inference/credential", json={"api_key": OTHER, "test": False}, headers=csrf(app))
    assert replaced.json()["fingerprint"] != out["fingerprint"] and "check" not in replaced.json()


def test_a_refused_key_is_reported_without_echoing_it(
    app: TestClient, oidc: FakeGitLabOIDC, keys: RecordingKeyAdmin
) -> None:
    use_jar(app, sign_in_as(app, oidc, "alice"))
    keys.healthy = False  # the fake gateway's error quotes the key, as a provider error may
    saved = app.put("/api/v1/inference/credential", json={"api_key": KEY}, headers=csrf(app))
    assert saved.status_code == 200
    check = saved.json()["check"]
    assert check["ok"] is False and "key_inert" in check["detail"] and KEY not in saved.text
    keys.healthy = True
    tested = app.post("/api/v1/inference/credential:test", headers=csrf(app))
    assert tested.status_code == 200 and tested.json()["check"]["ok"] is True


def test_malformed_keys_and_non_admins_are_refused(app: TestClient, oidc: FakeGitLabOIDC) -> None:
    use_jar(app, sign_in_as(app, oidc, "alice"))
    bad = app.put("/api/v1/inference/credential", json={"api_key": "sk-not-an-inferx-key-1234"}, headers=csrf(app))
    assert bad.status_code == 422 and "sk-not-an-inferx-key-1234" not in bad.text
    use_jar(app, sign_in_as(app, oidc, "bob"))
    assert app.get("/api/v1/inference/credential").status_code == 403
    denied = app.put("/api/v1/inference/credential", json={"api_key": KEY}, headers=csrf(app))
    assert denied.status_code == 403


def test_an_unreachable_gateway_is_shown_as_unknown(
    app: TestClient, oidc: FakeGitLabOIDC, keys: RecordingKeyAdmin
) -> None:
    use_jar(app, sign_in_as(app, oidc, "alice"))
    app.put("/api/v1/inference/credential", json={"api_key": KEY, "test": False}, headers=csrf(app))
    keys.reachable = False
    status = app.get("/api/v1/inference/credential").json()
    assert status["source"] == "unknown" and len(status["fingerprint"]) == 12


def test_a_gateway_whose_models_ignore_the_credential_is_refused(
    app: TestClient, oidc: FakeGitLabOIDC, keys: RecordingKeyAdmin
) -> None:
    use_jar(app, sign_in_as(app, oidc, "alice"))
    keys.without_credential = ["z-heavy"]  # an old config/litellm.yaml with api_key: os.environ/...
    refused = app.put("/api/v1/inference/credential", json={"api_key": KEY}, headers=csrf(app))
    assert refused.status_code == 409 and "litellm_credential_name: inferx" in refused.text
    assert "inferx" not in keys.credentials  # nothing sent to the gateway
