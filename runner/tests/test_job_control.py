"""Runner jobs: output repair, malformed arguments, loops, budgets, deadline, control (cancel, pause, guidance,
questions, key rotation), escalation and harness confirmation. Real OpenHandsBackend, fake LLM, fake z-server."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from runner.tests.conftest import Harness
from runner.tests.fake_internal import GOOD_SUMMARY, FakeJob, finish, tool_call, write_result

BAD_SUMMARY = {"title": "Release 2.4", "bullets": ["one", "two", "three", "four"]}  # no source, 4 bullets


def bodies(harness: Harness) -> list[dict[str, Any]]:
    return [r["body"] for r in harness.llm.requests() if r["path"] == "/v1/chat/completions"]


def test_output_repair_then_success(harness: Harness) -> None:
    harness.llm.add_rules(
        {"responses": [write_result(BAD_SUMMARY), finish("written"), write_result(GOOD_SUMMARY), finish("fixed")]}
    )
    run = harness.run(harness.spec())
    assert run.code == 0
    assert run.completion["status"] == "succeeded"
    assert run.completion["output"] == GOOD_SUMMARY
    errors = [e for e in run.events("validation_error") if e["where"] == "output"]
    assert len(errors) == 1 and errors[0]["attempt"] == 1
    paths = {e["path"] for e in errors[0]["errors"]}
    assert "(root)" in paths and "bullets" in paths  # missing source, too many bullets
    repair = json.dumps(bodies(harness)[2]["messages"][-1])
    assert "repair attempt 1 of 2" in repair and "source" in repair


def test_output_repair_exhausted_needs_attention(harness: Harness) -> None:
    harness.llm.add_rules({"responses": [write_result(BAD_SUMMARY), finish("written")], "repeat": True})
    base = harness.spec()
    assert base.output is not None
    spec = harness.spec(output={"schema": base.output.schema_, "max_repair_attempts": 1})
    run = harness.run(spec)
    assert run.code == 3
    assert run.completion["status"] == "needs_attention"
    assert run.completion["needs_attention_reason"] == "invalid_output"
    assert [e["attempt"] for e in run.events("validation_error") if e["where"] == "output"] == [1, 2]


def test_result_given_as_final_message_is_accepted(harness: Harness) -> None:
    harness.llm.add_rules({"responses": [finish("```json\n" + json.dumps(GOOD_SUMMARY) + "\n```")]})
    run = harness.run(harness.spec())
    assert run.completion["status"] == "succeeded"
    assert run.completion["output"] == GOOD_SUMMARY
    assert (run.workspace / "result.json").is_file()


def test_malformed_tool_arguments_yield_validation_error_and_recovery(harness: Harness) -> None:
    harness.llm.add_rules(
        {
            "responses": [
                tool_call("read", '{"path": "notes.md"'),  # truncated JSON
                tool_call("read", {"path": 42, "extra": True}),  # schema violation
                tool_call("read", {"path": "notes.md"}),
                write_result(GOOD_SUMMARY),
                finish(),
            ]
        }
    )
    run = harness.run(harness.spec())
    assert run.completion["status"] == "succeeded"
    errors = [e for e in run.events("validation_error") if e["where"] == "tool_args"]
    assert len(errors) == 2
    assert errors[0]["errors"][0]["path"] == "(arguments)"
    assert {e["path"] for e in errors[1]["errors"]} == {"path", "(root)"}
    results = run.events("tool_result")
    assert [r["ok"] for r in results[:3]] == [False, False, True]
    corrective = json.dumps(bodies(harness)[2]["messages"][-1])
    assert "match its JSON schema" in corrective


def test_loop_detected(harness: Harness) -> None:
    harness.llm.add_rules({"responses": [tool_call("read", {"path": "notes.md"})], "repeat": True})
    run = harness.run(harness.spec())
    assert run.code == 3
    assert run.completion["status"] == "needs_attention"
    assert run.completion["needs_attention_reason"] == "loop_detected"
    assert run.completion["steps"] == 3
    assert any("Loop detected" in m["text"] for m in run.events("message"))


def test_token_budget_exceeded(harness: Harness) -> None:
    harness.llm.add_rules(
        {"responses": [tool_call("read", {"path": "notes.md"}), tool_call("read", {"path": "."})], "repeat": True}
    )
    spec = harness.spec()
    spec = spec.model_copy(update={"limits": spec.limits.model_copy(update={"max_tokens": 1500})})
    run = harness.run(spec)
    assert run.code == 4
    assert run.completion["status"] == "budget_exceeded"
    assert run.completion["tokens_in"] + run.completion["tokens_out"] >= 1500
    assert run.completion["error"]["class"] == "budget"


def test_step_limit(harness: Harness) -> None:
    harness.llm.add_rules(
        {
            "responses": [
                tool_call("read", {"path": "notes.md"}),
                tool_call("read", {"path": "."}),
                tool_call("think", {"thought": "hmm"}),
            ],
            "repeat": True,
        }
    )
    spec = harness.spec()
    spec = spec.model_copy(update={"limits": spec.limits.model_copy(update={"max_steps": 2})})
    run = harness.run(spec)
    assert run.code == 4
    assert run.completion["status"] == "budget_exceeded"
    assert run.completion["steps"] == 2
    assert run.completion["error"]["class"] == "steps"


def test_deadline_times_out(harness: Harness) -> None:
    harness.llm.add_rules(
        {
            "responses": [
                {"delay_ms": 700, "then": tool_call("read", {"path": "notes.md"})},
                {"delay_ms": 700, "then": tool_call("read", {"path": "."})},
                {"delay_ms": 700, "then": tool_call("think", {"thought": "still thinking"})},
            ],
            "repeat": True,
        }
    )
    spec = harness.spec(deadline=(datetime.now(UTC) + timedelta(seconds=2)).isoformat())
    run = harness.run(spec)
    assert run.code == 5
    assert run.completion["status"] == "timed_out"
    assert 1 <= run.completion["steps"] <= 4


def test_cancel_via_control(harness: Harness) -> None:
    harness.llm.add_rules(
        {"responses": [tool_call("read", {"path": "notes.md"}), tool_call("read", {"path": "."})], "repeat": True}
    )
    spec = harness.spec()
    job = harness.add(spec)

    def cancel_after_first(job_: FakeJob, record: dict[str, Any]) -> None:
        if record["step"] == 1:
            job_.state, job_.reason = "cancel", "user stopped it"

    job.on_checkpoint = cancel_after_first
    run = harness.run(spec, job)
    assert run.code == 6
    assert run.completion["status"] == "cancelled"
    assert run.completion["steps"] == 1
    assert len(bodies(harness)) == 1


def test_pause_parks_then_resumes(harness: Harness) -> None:
    harness.llm.add_rules(
        {"responses": [tool_call("read", {"path": "notes.md"}), write_result(GOOD_SUMMARY), finish()]}
    )
    spec = harness.spec()
    job = harness.add(spec)
    calls_while_paused: list[int] = []

    def pause_after_first(job_: FakeJob, record: dict[str, Any]) -> None:
        if record["step"] == 1:
            job_.state = "pause"

    def resume_after_polls(job_: FakeJob) -> None:
        if job_.state == "pause":
            calls_while_paused.append(len(bodies(harness)))
            if job_.control_polls >= 3:
                job_.state = "run"

    job.on_checkpoint = pause_after_first
    job.on_poll = resume_after_polls
    run = harness.run(spec, job)
    assert run.completion["status"] == "succeeded"
    statuses = [(s["from"], s["to"]) for s in run.events("status")]
    assert statuses == [("running", "paused"), ("paused", "running")]
    assert calls_while_paused and set(calls_while_paused) == {1}  # no model call while paused
    assert len(bodies(harness)) == 3


def test_guide_message_is_injected_once(harness: Harness) -> None:
    harness.llm.add_rules(
        {"responses": [tool_call("read", {"path": "notes.md"}), write_result(GOOD_SUMMARY), finish()]}
    )
    spec = harness.spec()
    job = harness.add(spec)

    def guide(job_: FakeJob, record: dict[str, Any]) -> None:
        if record["step"] == 1:
            job_.messages.append({"id": "msg-1", "text": "Mention SSO in the title.", "by": "alice"})

    job.on_checkpoint = guide  # the fake keeps returning the message: the runner must not repeat it
    run = harness.run(spec, job)
    assert run.completion["status"] == "succeeded"
    sent = bodies(harness)
    assert "Mention SSO in the title." not in json.dumps(sent[0]["messages"])
    second = json.dumps(sent[1]["messages"])
    assert "Guidance from alice" in second
    assert json.dumps(sent[-1]["messages"]).count("Mention SSO in the title.") == 1


def test_ask_human_blocks_until_answered(harness: Harness) -> None:
    harness.llm.add_rules(
        {
            "responses": [
                tool_call("ask_human", {"question": "Which file?", "options": ["notes.md", "other.md"]}),
                write_result(GOOD_SUMMARY),
                finish(),
            ]
        }
    )
    spec = harness.spec()
    job = harness.add(spec)

    def answer_later(job_: FakeJob) -> None:
        if job_.questions and job_.control_polls >= 4 and not job_.answers:
            job_.answers.append({"question_id": job_.questions[0]["id"], "answer": "notes.md", "answered_by": "bob"})

    job.on_poll = answer_later
    run = harness.run(spec, job)
    assert run.completion["status"] == "succeeded"
    assert job.control_polls >= 4
    question = run.events("question")[0]
    assert question["text"] == "Which file?" and question["options"] == ["notes.md", "other.md"]
    assert question["question_id"] == job.questions[0]["id"]
    second = json.dumps(bodies(harness)[1]["messages"][-1])
    assert "Answer from bob: notes.md" in second
    assert "Answer to your question" not in json.dumps(bodies(harness)[-1]["messages"])  # delivered once


def test_llm_key_rotation(harness: Harness) -> None:
    harness.llm.add_rules(
        {"responses": [tool_call("read", {"path": "notes.md"}), write_result(GOOD_SUMMARY), finish()]}
    )
    spec = harness.spec()
    job = harness.add(spec)
    rotated = "sk-rotated-" + "b" * 24

    def rotate(job_: FakeJob, record: dict[str, Any]) -> None:
        if record["step"] == 1:
            job_.llm_api_key = rotated

    job.on_checkpoint = rotate
    run = harness.run(spec, job)
    assert run.completion["status"] == "succeeded"
    keys = [r["headers"]["authorization"] for r in harness.llm.requests()]
    assert keys[0] == f"Bearer {spec.llm.api_key}"
    assert keys[1:] == [f"Bearer {rotated}"] * 2
    assert rotated not in json.dumps(job.events)


def test_escalation_after_repeated_failures(harness: Harness) -> None:
    harness.llm.add_rules(
        {
            "responses": [
                tool_call("read", {"path": "missing-1.md"}),
                tool_call("read", {"path": "missing-2.md"}),
                tool_call("read", {"path": "missing-3.md"}),
                tool_call("read", {"path": "notes.md"}),
                write_result(GOOD_SUMMARY),
                finish(),
            ]
        }
    )
    run = harness.run(harness.spec())
    assert run.completion["status"] == "succeeded"
    models = [b["model"] for b in bodies(harness)]
    assert models[:3] == ["z-fast"] * 3 and set(models[3:]) == {"z-heavy"}
    assert sum("Escalated from z-fast to z-heavy" in m["text"] for m in run.events("message")) == 1
    assert {u["model"] for u in run.events("usage")} == {"z-fast", "z-heavy"}


def test_harness_confirmation_needs_attention(harness: Harness) -> None:
    harness.llm.add_rules({"responses": [tool_call("terminal", {"command": "rm -rf /"})]})
    spec = harness.spec(tools={"local": ["read", "terminal"], "builtins": [], "gateway": []})
    run = harness.run(spec)
    assert run.code == 3
    assert run.completion["status"] == "needs_attention"
    assert run.completion["needs_attention_reason"] == "guardrail_confirm"
    denied = run.events("denied")
    assert denied and denied[0]["rule"] == "guardrail_confirm" and denied[0]["what"] == "terminal"
    assert not run.events("terminal")  # never executed


def test_action_status_changes_reach_the_agent(harness: Harness) -> None:
    harness.llm.add_rules(
        {"responses": [tool_call("read", {"path": "notes.md"}), tool_call("read", {"path": "."}), finish()]}
    )
    spec = harness.spec(output=None)
    job = harness.add(spec)

    def approve(job_: FakeJob, record: dict[str, Any]) -> None:
        if record["step"] == 1:
            job_.action_states = [
                {"action_id": "act-1", "status": "pending"},
                {"action_id": "act-2", "status": "applied"},
            ]

    job.on_checkpoint = approve
    run = harness.run(spec, job)
    assert run.completion["status"] == "succeeded"
    second = json.dumps(bodies(harness)[1]["messages"])
    assert "act-2 is applied" in second and "act-1" not in second
    assert json.dumps(bodies(harness)[2]["messages"]).count("act-2 is applied") == 1
