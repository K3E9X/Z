"""Resume after a crash: a real `z-runner` process is killed mid-step, a new runner (fresh workspace, as on a new
pod) starts from the last checkpoint and finishes without redoing the finished steps."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from runner.tests.conftest import Harness
from runner.tests.fake_internal import GOOD_SUMMARY, FakeJob, finish, tool_call, write_result


def _wait(predicate: Any, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.05)


def test_kill_mid_job_then_resume_from_checkpoint(harness: Harness, tmp_path: Path) -> None:
    harness.llm.add_rules(
        {
            "responses": [
                tool_call("terminal", {"command": "echo step1 >> counter.txt"}),
                tool_call("read", {"path": "counter.txt"}),
                {"delay_ms": 4000, "then": tool_call("terminal", {"command": "echo must-not-run >> counter.txt"})},
                write_result(GOOD_SUMMARY),
                finish("resumed and finished"),
            ]
        }
    )
    tools = {"local": ["read", "edit", "terminal"], "builtins": [], "gateway": []}
    spec = harness.spec(tools=tools)
    job = harness.add(spec)
    second_checkpoint = threading.Event()

    def on_checkpoint(job_: FakeJob, record: dict[str, Any]) -> None:
        if record["step"] == 2:
            second_checkpoint.set()

    job.on_checkpoint = on_checkpoint
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("Z_")},
        "Z_SERVER_URL": harness.server.url,
        "Z_JOB_ID": spec.job_id,
        "Z_JOB_TOKEN": job.bootstrap_token,
    }
    first_workspace = tmp_path / "pod-1-workspace"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "z_runner",
            "--workspace",
            str(first_workspace),
            "--state-dir",
            str(tmp_path / "pod-1-state"),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        assert second_checkpoint.wait(90), "the first runner never reached step 2"
        _wait(lambda: len(harness.llm.requests()) == 3, 30, "the third model call")
        process.send_signal(signal.SIGKILL)  # crash in the middle of step 3
        process.wait(30)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == -signal.SIGKILL
    assert job.completion is None
    assert [c["step"] for c in job.checkpoints] == [1, 2]
    assert (first_workspace / "counter.txt").read_text() == "step1\n"

    # a new pod: new bootstrap and session tokens, empty workspace, the last checkpoint in the spec
    resumed_spec = harness.spec(
        job_id=spec.job_id,
        tools=tools,
        checkpoint_b64=job.checkpoints[-1]["blob_b64"],
        resume_from_step=2,
        attempt=2,
    )
    resumed_job = harness.add(resumed_spec)
    run = harness.run(resumed_spec, resumed_job, workspace=tmp_path / "pod-2-workspace")

    assert run.code == 0, run.completion
    assert run.completion["status"] == "succeeded"
    assert run.completion["output"] == GOOD_SUMMARY
    assert run.completion["steps"] == 4  # 2 before the crash + 2 after
    assert (run.workspace / "counter.txt").read_text() == "step1\n"  # restored, and step 1 was not redone
    assert not run.events("terminal")
    assert [c["step"] for c in resumed_job.checkpoints] == [3, 4]
    assert any("Resumed from the checkpoint of step 2" in m["text"] for m in run.events("message"))

    requests = harness.llm.requests()
    assert len(requests) == 5  # 3 by the killed runner, 2 by the resumed one
    history = json.dumps(requests[3]["body"]["messages"])
    assert "Apply the summarize skill" in history  # the original task is still in the conversation
    assert "step1" in history  # and the result of the read done before the crash
    assert history.count("echo step1 >> counter.txt") == 1
