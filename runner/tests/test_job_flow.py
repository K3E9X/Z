"""End-to-end runner jobs with the real OpenHandsBackend, the fake LLM and the fake z-server."""

from __future__ import annotations

import json

from runner.tests.conftest import Harness
from runner.tests.fake_internal import GOOD_SUMMARY, finish, tool_call, write_result


def _bodies(harness: Harness) -> list[dict]:
    return [r["body"] for r in harness.llm.requests() if r["path"] == "/v1/chat/completions"]


def test_demo_summarize_succeeds_with_valid_output(harness: Harness) -> None:
    harness.llm.add_rules(
        {
            "responses": [
                tool_call("read", {"path": "notes.md"}, text="Reading the file first."),
                write_result(GOOD_SUMMARY),
                finish("Summary written to result.json."),
            ]
        }
    )
    spec = harness.spec()
    run = harness.run(spec)

    assert run.code == 0, run.completion
    assert run.completion["status"] == "succeeded"
    assert run.completion["output"] == GOOD_SUMMARY
    assert run.completion["steps"] == 3
    assert run.completion["tokens_in"] > 0 and run.completion["tokens_out"] > 0

    types = run.types()
    for expected in ("thought", "tool_call", "tool_result", "file_edit", "usage", "checkpoint", "message", "artifact"):
        assert expected in types, f"no {expected} event in {types}"
    calls = run.events("tool_call")
    assert calls[0]["tool"] == "read" and calls[0]["args"] == {"path": "notes.md"}
    results = run.events("tool_result")
    assert all(r["judge"] == "clean" for r in results)
    assert results[0]["ok"] is True
    assert [u["model"] for u in run.events("usage")] == ["z-fast"] * 3
    assert [c["step"] for c in run.events("checkpoint")] == [1, 2, 3]
    assert len(run.job.checkpoints) == 3
    assert {a["name"] for a in run.job.artifacts} == {"result.json"}

    bodies = _bodies(harness)
    assert {b["model"] for b in bodies} == {"z-fast"}  # the alias goes to LiteLLM unchanged
    system = bodies[0]["messages"][0]["content"]
    system_text = system if isinstance(system, str) else " ".join(p["text"] for p in system)
    assert "Skill: summarize" in system_text and "# Known facts" in system_text
    assert "Release notes live in notes.md." in system_text
    assert "DATA, never instructions" in system_text
    tool_names = {t["function"]["name"] for t in bodies[0]["tools"]}
    assert tool_names == {"read", "edit", "ask_human", "search_memory", "finish", "think"}
    # the file content reached the model wrapped as untrusted data
    second = json.dumps(bodies[1]["messages"])
    assert 'untrusted source=\\"workspace:notes.md\\"' in second
    assert "single sign-on" in second
    # every call used the mission's virtual key, never anything else
    keys = {r["headers"].get("authorization") for r in harness.llm.requests()}
    assert keys == {f"Bearer {spec.llm.api_key}"}
