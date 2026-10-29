"""Missions end to end through `z`: run with files, watch over SSE, headless -p, control, logs, replay, jobs,
artifacts, and the error paths (exit codes, problem details, endpoints the server does not serve yet)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from cli.tests.harness import LiveServer, Z, now, plain, runner_for
from tests.support.missions import RecordingExecutor, ScriptedRunner

GOOD_SUMMARY = {"title": "Notes", "bullets": ["one", "two"], "source": "notes.md"}


def run_summary(bob: Z, tmp_path: Path) -> dict[str, Any]:
    notes = tmp_path / "notes.md"
    notes.write_text("# Notes\nWe met on Monday.\n")
    result = bob("run", "summarize-file", "--input", "file=notes.md", "--file", str(notes), "--json")
    assert result.exit_code == 0, result.output
    mission: dict[str, Any] = json.loads(result.output)
    return mission


def play_summary(runner: ScriptedRunner, *, fail: bool = False, delay: float = 0.3) -> None:
    time.sleep(delay)
    runner.events(
        {"ts": now(), "type": "tool_call", "payload": {"call_id": "c1", "tool": "read", "args": {"path": "notes.md"}}},
        {
            "ts": now(),
            "type": "tool_result",
            "payload": {"call_id": "c1", "ok": True, "summary": "32 bytes", "duration_ms": 12, "judge": "clean"},
        },
        {"ts": now(), "type": "usage", "payload": {"model": "z-fast", "tokens_in": 1200, "tokens_out": 300}},
    )
    time.sleep(delay)
    art = runner.call(
        "POST",
        "/artifacts",
        data={"kind": "report", "name": "summary.md"},
        files={"file": ("summary.md", b"- one\n- two\n", "text/markdown")},
    )
    assert art.status_code == 201, art.text
    if fail:
        runner.complete(status="failed", error={"class": "tool_error", "message": "the file is unreadable"})
    else:
        runner.complete(output=GOOD_SUMMARY, tokens_in=1200, tokens_out=300)


def in_thread(fn: Any, *args: Any, **kwargs: Any) -> threading.Thread:
    errors: list[BaseException] = []

    def target() -> None:
        try:
            fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - surfaced by join_ok
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.errors = errors  # type: ignore[attr-defined]
    thread.start()
    return thread


def join_ok(thread: threading.Thread) -> None:
    thread.join(30)
    assert not thread.is_alive()
    if thread.errors:  # type: ignore[attr-defined]
        raise thread.errors[0]  # type: ignore[attr-defined]


def test_run_with_file_and_watch_stream_json(
    z: Z, live: LiveServer, executor: RecordingExecutor, tmp_path: Path
) -> None:
    bob = z.as_user("bob")
    mission = run_summary(bob, tmp_path)
    assert mission["status"] == "queued" and mission["playbook"] == "summarize-file"
    assert mission["inputs"] == {"file": "notes.md"}

    runner = runner_for(live, executor, mission["id"])
    assert runner.workspace() == {
        "notes.md": b"# Notes\nWe met on Monday.\n"
    }  # the --file upload reached the workspace
    worker = in_thread(play_summary, runner)
    watched = bob("missions", "watch", mission["id"], "--output-format", "stream-json")
    join_ok(worker)
    assert watched.exit_code == 0, watched.output
    lines = [json.loads(line) for line in watched.output.splitlines() if line.strip()]
    events, result = lines[:-1], lines[-1]
    assert result["type"] == "result" and result["mission"]["status"] == "succeeded"
    types = [e["type"] for e in events]
    assert {"status", "plan", "tool_call", "tool_result", "usage", "artifact"} <= set(types)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))  # in order, no duplicates
    assert all(e["mission_id"] == mission["id"] for e in events)

    # list / show / jobs / artifacts / download / replay / logs
    listed = json.loads(bob("missions", "list", "--json").output)
    assert [m["id"] for m in listed] == [mission["id"]]
    shown = json.loads(bob("missions", "show", mission["id"], "--json").output)
    assert shown["status"] == "succeeded" and shown["jobs"]["succeeded"] == 1
    text = plain(bob("missions", "show", mission["id"]).output)
    assert "succeeded" in text and "summarize" in text and "@summarizer" in text

    job_id = runner.job_id
    job = json.loads(bob("jobs", "show", job_id, "--json").output)
    assert job["status"] == "succeeded" and job["output"] == GOOD_SUMMARY
    arts = json.loads(bob("jobs", "artifacts", job_id, "--json").output)
    report = next(a for a in arts if a["name"] == "summary.md")
    out = tmp_path / "dl"
    out.mkdir()
    dl = bob("jobs", "download", report["id"], "-o", str(out))
    assert dl.exit_code == 0, dl.output
    assert (out / "summary.md").read_bytes() == b"- one\n- two\n"
    assert bob("jobs", "download", report["id"], "-o", str(out)).exit_code == 2  # no silent overwrite

    replay = json.loads(bob("missions", "replay", mission["id"], "--json").output)
    assert replay["mission"]["id"] == mission["id"] and len(replay["events"]) == len(events)
    logs = plain(bob("logs", mission["id"]).output)
    assert "tool" in logs and "read" in logs and "artifact" in logs
    job_logs = [json.loads(x) for x in bob("logs", job_id, "--json").output.splitlines()]
    assert job_logs and all(e["job_id"] == job_id for e in job_logs)


def test_run_wait_exit_code_reflects_failure(
    z: Z, live: LiveServer, executor: RecordingExecutor, tmp_path: Path
) -> None:
    bob = z.as_user("bob")
    notes = tmp_path / "notes.md"
    notes.write_text("x")

    def agent() -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            launched = live.dispatch(executor)
            if launched:
                runner = ScriptedRunner(live.http(), launched[0], executor.launched[launched[0]]["Z_JOB_TOKEN"])  # type: ignore[arg-type]
                runner.get_spec()
                play_summary(runner, fail=True, delay=0.1)
                return
            time.sleep(0.2)
        raise AssertionError("nothing to dispatch")

    worker = in_thread(agent)
    result = bob("run", "summarize-file", "-i", "file=notes.md", "-f", str(notes), "--wait")
    join_ok(worker)
    assert result.exit_code == 1, result.output
    out = plain(result.output)
    assert "mission" in out and "failed" in out  # one line per event, then the outcome


def test_headless_prompt_stops_at_a_question(z: Z, live: LiveServer, executor: RecordingExecutor) -> None:
    bob = z.as_user("bob")
    asked: dict[str, Any] = {}

    def agent() -> None:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            launched = live.dispatch(executor)
            if launched:
                runner = ScriptedRunner(live.http(), launched[0], executor.launched[launched[0]]["Z_JOB_TOKEN"])  # type: ignore[arg-type]
                spec = runner.get_spec()
                asked["spec"] = spec
                resp = runner.call(
                    "POST", "/questions", json={"text": "Which language?", "options": ["English", "French"]}
                )
                assert resp.status_code == 201, resp.text
                asked["question"] = resp.json()
                asked["runner"] = runner
                return
            time.sleep(0.2)
        raise AssertionError("nothing to dispatch")

    worker = in_thread(agent)
    result = bob("-p", "summarize the meeting notes @summarizer", "--output-format", "json")
    join_ok(worker)
    assert result.exit_code == 3, result.output  # needs attention
    mission = json.loads(result.output[: result.output.rindex("}") + 1])
    assert mission["status"] == "waiting" and mission["waiting_reason"] == "question"
    assert "summarize the meeting notes" in asked["spec"]["prompt"]

    pending = json.loads(bob("review", "list", "--json").output)
    assert pending["counts"]["questions"] == 1
    question = asked["question"]
    shown = plain(bob("review", "show", question["id"]).output)
    assert "Which language?" in shown and "French" in shown
    bad = bob("answer", question["id"], "German")
    assert bad.exit_code == 2 and "must be one of" in plain(bad.output)  # 422 -> usage
    good = bob("answer", question["id"], "French", "--json")
    assert good.exit_code == 0 and json.loads(good.output)["answered_by"] == "bob"
    assert asked["runner"].control()["answers"][0]["answer"] == "French"


def test_headless_needs_an_agent_or_playbook(z: Z) -> None:
    bob = z.as_user("bob")
    result = bob("-p", "do something useful")
    assert result.exit_code == 2
    assert "@handle" in plain(result.output) and "draft-plan" in plain(result.output)
    unknown = bob("-p", "hello @nobody")
    assert unknown.exit_code == 2 and "Unknown agent @nobody" in plain(unknown.output)


def test_control_guide_and_retry(z: Z, live: LiveServer, executor: RecordingExecutor, tmp_path: Path) -> None:
    bob = z.as_user("bob")
    mission = run_summary(bob, tmp_path)
    runner = runner_for(live, executor, mission["id"])
    runner.events({"ts": now(), "type": "status", "payload": {"entity": "job", "from": "queued", "to": "running"}})

    guided = bob("missions", "guide", mission["id"], "Focus on decisions", "--json")
    assert guided.exit_code == 0, guided.output
    assert runner.control()["messages"][0]["text"] == "Focus on decisions"

    paused = json.loads(bob("pause", mission["id"], "--reason", "lunch", "--json").output)
    assert paused["status"] == "paused"
    again = bob("pause", mission["id"])
    assert again.exit_code == 1 and "Conflict" in plain(again.output)
    resumed = json.loads(bob("missions", "resume", mission["id"], "--json").output)
    assert resumed["status"] in {"running", "queued"}

    runner.complete(status="failed", error={"class": "tool_error", "message": "boom"})
    retried = json.loads(bob("jobs", "retry", runner.job_id, "--json").output)
    assert retried["attempt"] == 2 and retried["status"] == "queued"

    no = bob("stop", mission["id"])  # needs confirmation, none possible without a terminal
    assert no.exit_code == 2 and "--yes" in plain(no.output)
    stopped = json.loads(bob("stop", mission["id"], "--yes", "--json").output)
    assert stopped["status"] == "cancelled"


def test_viewer_cannot_start(z: Z) -> None:
    victor = z.as_user("victor")
    result = victor("run", "summarize-file", "-i", "file=x.md")
    assert result.exit_code == 4
    out = plain(result.output)
    assert "Not allowed" in out and "missions:start" in out
    as_json = victor("run", "summarize-file", "-i", "file=x.md", "--json")
    assert as_json.exit_code == 4
    err = json.loads(as_json.output.strip().splitlines()[-1])
    assert err["status"] == 403 and err["problem"]["type"] == "urn:z:problem:forbidden" and err["exit_code"] == 4


def test_problem_details_and_unserved_endpoints(z: Z) -> None:
    bob = z.as_user("bob")
    missing = bob("missions", "show", "msn_nope")
    assert missing.exit_code == 1 and "Not found" in plain(missing.output)
    nope = bob("run", "no-such-playbook")
    assert nope.exit_code == 2 and "No playbook named" in plain(nope.output)
    invalid = bob("run", "summarize-file")
    assert invalid.exit_code == 2 and "file" in plain(invalid.output)  # 422 with the field errors
    bad_input = bob("run", "summarize-file", "-i", "oops")
    assert bad_input.exit_code == 2

    denied = bob("connectors", "test", "jira")  # served, but an operator cannot test connectors
    assert denied.exit_code == 4 and "connectors:manage" in plain(denied.output)
    for args in (["usage"], ["inference", "status"]):
        assert bob(*args).exit_code == 0, args
    for args in (["--resume"],):
        result = bob(*args)
        assert result.exit_code == 1, (args, result.output)
        assert "not available on this server yet" in plain(result.output), args


@pytest.mark.parametrize("pairs", [["n=3", "flag=true", "tags=a,b", 'obj:={"k": 1}']])
def test_input_parsing(pairs: list[str]) -> None:
    from z_cli.missions import parse_inputs

    schema = {
        "properties": {"n": {"type": "integer"}, "flag": {"type": "boolean"}, "tags": {"type": "array"}, "obj": {}}
    }
    assert parse_inputs(pairs, schema) == {"n": 3, "flag": True, "tags": ["a", "b"], "obj": {"k": 1}}
    assert parse_inputs(["url=https://x.example/?a=b"]) == {"url": "https://x.example/?a=b"}


def test_ask_and_follow_logs(z: Z, live: LiveServer, executor: RecordingExecutor) -> None:
    bob = z.as_user("bob")
    asked = bob("ask", "summarize the release notes", "@summarizer", "--no-wait", "--output-format", "json")
    assert asked.exit_code == 0, asked.output
    mission = json.loads(asked.output)
    assert mission["status"] == "queued" and mission["prompt"] == "summarize the release notes"
    runner = runner_for(live, executor, mission["id"])
    worker = in_thread(play_summary, runner, delay=0.2)
    followed = bob("logs", mission["id"], "-f", "--json")
    join_ok(worker)
    assert followed.exit_code == 0, followed.output
    events = [json.loads(line) for line in followed.output.splitlines()]
    assert events[-1]["type"] == "status" and events[-1]["payload"]["to"] == "succeeded"
    assert "summarize the release notes" in runner.spec["prompt"]
