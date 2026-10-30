from __future__ import annotations

import base64
import json
import math
import struct
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.fakes.fake_llm import FakeLLM


@pytest.fixture(scope="module")
def llm() -> Iterator[FakeLLM]:
    with FakeLLM() as server:
        yield server


@pytest.fixture(autouse=True)
def _reset(llm: FakeLLM) -> None:
    llm.reset()


def chat(llm: FakeLLM, content: str = "hello", **extra: object) -> httpx.Response:
    body = {"model": "z-fast", "messages": [{"role": "user", "content": content}], **extra}
    return httpx.post(f"{llm.base_url}/chat/completions", json=body, headers={"Authorization": "Bearer any"})


def test_default_text_and_usage(llm: FakeLLM) -> None:
    r = chat(llm, "x" * 400)
    assert r.status_code == 200
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "ok"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["prompt_tokens"] == 100
    assert data["usage"]["total_tokens"] == data["usage"]["prompt_tokens"] + data["usage"]["completion_tokens"]


def test_models_lists_aliases(llm: FakeLLM) -> None:
    ids = [m["id"] for m in httpx.get(f"{llm.base_url}/models").json()["data"]]
    assert {"z-heavy", "z-fast", "z-judge", "z-embed"} <= set(ids)


def test_tool_call_and_malformed_tool_call(llm: FakeLLM) -> None:
    llm.add_rules(
        [
            {
                "match": {"contains": "findings"},
                "responses": [
                    {"tool_calls": [{"name": "defectdojo_findings_list", "arguments": {"limit": 5}}]},
                    {"tool_calls": [{"name": "defectdojo_findings_list", "arguments": '{"limit": 5'}]},
                ],
            },
        ]
    )
    first = chat(llm, "list findings").json()["choices"][0]
    assert first["finish_reason"] == "tool_calls"
    call = first["message"]["tool_calls"][0]
    assert call["function"]["name"] == "defectdojo_findings_list"
    assert json.loads(call["function"]["arguments"]) == {"limit": 5}
    second = chat(llm, "list findings").json()["choices"][0]["message"]["tool_calls"][0]
    with pytest.raises(json.JSONDecodeError):
        json.loads(second["function"]["arguments"])
    # Rule exhausted (no repeat): falls back to the default.
    assert chat(llm, "list findings").json()["choices"][0]["message"]["content"] == "ok"


def test_rules_order_sequence_and_repeat(llm: FakeLLM) -> None:
    llm.add_rules({"id": "old", "match": {"model": "z-.*"}, "responses": [{"text": "old"}], "repeat": True})
    llm.add_rules(
        {
            "id": "new",
            "match": {"last_role": "user", "has_tools": False},
            "responses": [{"text": "a"}, {"text": "b"}],
            "repeat": True,
        }
    )
    texts = [chat(llm).json()["choices"][0]["message"]["content"] for _ in range(3)]
    assert texts == ["a", "b", "b"]  # later rule wins, sequence, last one repeats
    with_tools = chat(llm, tools=[{"type": "function", "function": {"name": "t", "parameters": {}}}])
    assert with_tools.json()["choices"][0]["message"]["content"] == "old"
    llm.add_rules({"match": {"system_contains": "judge"}, "responses": [{"json": {"verdict": "clean"}}]})
    body = {
        "model": "z-judge",
        "messages": [{"role": "system", "content": "You are the judge"}, {"role": "user", "content": "scan"}],
    }
    content = httpx.post(f"{llm.base_url}/chat/completions", json=body).json()["choices"][0]["message"]["content"]
    assert json.loads(content) == {"verdict": "clean"}


def test_error_and_delay(llm: FakeLLM) -> None:
    llm.add_rules({"match": {"contains": "boom"}, "responses": [{"error": {"status": 429, "message": "slow down"}}]})
    llm.add_rules({"match": {"contains": "slow"}, "responses": [{"delay_ms": 200, "then": {"text": "late"}}]})
    r = chat(llm, "boom")
    assert r.status_code == 429 and r.json()["error"]["message"] == "slow down"
    r = chat(llm, "slow")
    assert r.json()["choices"][0]["message"]["content"] == "late"
    assert r.elapsed.total_seconds() >= 0.2


def _stream(llm: FakeLLM, content: str) -> list[dict[str, object]]:
    body = {
        "model": "z-heavy",
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": content}],
    }
    chunks = []
    with httpx.stream("POST", f"{llm.base_url}/chat/completions", json=body) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in r.iter_lines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    for line in lines[:-1]:
        chunks.append(json.loads(line[6:]))
    return chunks


def test_streaming_text_and_tool_calls(llm: FakeLLM) -> None:
    llm.add_rules(
        {"match": {"contains": "story"}, "responses": [{"text": "A fairly long streamed answer, in pieces."}]}
    )
    llm.add_rules(
        {
            "match": {"contains": "call"},
            "responses": [
                {
                    "tool_calls": [
                        {"name": "jira_search", "arguments": {"jql": "project = SECOPS"}},
                        {"name": "jira_get_issue", "arguments": {"issue_key": "SECOPS-4791"}},
                    ]
                }
            ],
        }
    )
    chunks = _stream(llm, "tell a story")
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert text == "A fairly long streamed answer, in pieces."
    assert chunks[-1]["usage"]["completion_tokens"] > 0

    calls: dict[int, dict[str, str]] = {}
    finish = None
    for c in _stream(llm, "please call tools"):
        for choice in c["choices"]:
            finish = choice["finish_reason"] or finish
            for tc in choice["delta"].get("tool_calls", []):
                slot = calls.setdefault(tc["index"], {"name": "", "arguments": ""})
                slot["name"] += tc.get("function", {}).get("name", "")
                slot["arguments"] += tc.get("function", {}).get("arguments", "")
    assert finish == "tool_calls"
    assert calls[0]["name"] == "jira_search" and json.loads(calls[0]["arguments"]) == {"jql": "project = SECOPS"}
    assert json.loads(calls[1]["arguments"]) == {"issue_key": "SECOPS-4791"}


def test_embeddings_deterministic_unit_vectors(llm: FakeLLM) -> None:
    def emb(**kw: object) -> list[dict[str, object]]:
        r = httpx.post(f"{llm.base_url}/embeddings", json={"model": "z-embed", **kw})
        return r.json()["data"]

    a, b = emb(input=["log4j", "openssl"])
    again = emb(input="log4j")[0]
    assert a["embedding"] == again["embedding"] and a["embedding"] != b["embedding"]
    vec = a["embedding"]
    assert len(vec) == 1024
    assert math.isclose(sum(v * v for v in vec), 1.0, rel_tol=1e-6)
    packed = emb(input="log4j", encoding_format="base64", dimensions=8)[0]["embedding"]
    floats = struct.unpack("<8f", base64.b64decode(packed))
    assert math.isclose(sum(v * v for v in floats), 1.0, rel_tol=1e-5)


def test_request_log(llm: FakeLLM) -> None:
    chat(llm, "logged")
    log = llm.requests()
    assert log[-1]["model"] == "z-fast"
    assert log[-1]["body"]["messages"][0]["content"] == "logged"
    assert log[-1]["headers"]["authorization"] == "Bearer any"
    llm.clear_requests()
    assert llm.requests() == []


def test_rules_file(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text("rules:\n  - match: {model: z-heavy}\n    responses: [{text: from-yaml}]\n    repeat: true\n")
    with FakeLLM(rules_file=str(rules)) as server:
        body = {"model": "z-heavy", "messages": [{"role": "user", "content": "hi"}]}
        r = httpx.post(f"{server.base_url}/chat/completions", json=body)
        assert r.json()["choices"][0]["message"]["content"] == "from-yaml"
