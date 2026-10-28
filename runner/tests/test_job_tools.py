"""Tools end to end: documents, code, search_memory, spawn_subagent, the patch of a git_clone workspace, the
sandbox environment and SIGTERM handling."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
from z_runner.runner import EXIT_INTERRUPTED, Runner, RunnerConfig

from runner.tests.conftest import Harness
from runner.tests.fake_internal import FakeJob, finish, make_manifest, tool_call

ALL_LOCAL = {"local": ["read", "edit", "terminal", "code", "documents"], "builtins": [], "gateway": []}


def test_documents_tool_produces_artifacts(harness: Harness) -> None:
    sections = [
        {"heading": "Findings", "paragraphs": ["Two issues."], "bullets": ["log4j", "openssl"]},
        {"heading": "Table", "table": {"headers": ["id", "severity"], "rows": [["1001", "critical"], ["1002", 7.5]]}},
    ]
    harness.llm.add_rules(
        {
            "responses": [
                tool_call(
                    "documents", {"format": "docx", "name": "report", "title": "Weekly report", "sections": sections}
                ),
                tool_call(
                    "documents", {"format": "pdf", "name": "report", "title": "Weekly report", "sections": sections}
                ),
                tool_call(
                    "documents",
                    {
                        "format": "xlsx",
                        "name": "findings",
                        "headers": ["id", "cvss"],
                        "rows": [["1001", 9.8], ["1002", 7.5]],
                    },
                ),
                tool_call("documents", {"format": "csv", "name": "findings", "headers": ["id"], "rows": [["1001"]]}),
                tool_call("documents", {"format": "md", "name": "summary", "title": "Summary", "sections": sections}),
                tool_call(
                    "documents", {"format": "svg", "name": "evil", "content": "<svg><script>alert(1)</script></svg>"}
                ),
                tool_call("documents", {"format": "mermaid", "name": "flow", "content": "graph TD; A-->B"}),
                finish(),
            ]
        }
    )
    run = harness.run(harness.spec(output=None, tools=ALL_LOCAL))
    assert run.completion["status"] == "succeeded"
    by_name = {a["name"]: a for a in run.job.artifacts}
    assert set(by_name) == {"report.docx", "report.pdf", "findings.xlsx", "findings.csv", "summary.md", "flow.mmd"}
    assert by_name["findings.csv"]["kind"] == "data" and by_name["report.docx"]["kind"] == "document"
    import docx

    document = docx.Document(io.BytesIO(by_name["report.docx"]["content"]))
    assert "Weekly report" in [p.text for p in document.paragraphs]
    assert document.tables[0].cell(1, 1).text == "critical"
    assert by_name["report.pdf"]["content"].startswith(b"%PDF")
    with zipfile.ZipFile(io.BytesIO(by_name["findings.xlsx"]["content"])) as xlsx:
        sheet = xlsx.read("xl/worksheets/sheet1.xml").decode()
    assert "1001" in sheet and "<v>9.8</v>" in sheet
    assert "| 1002 | 7.5 |" in by_name["summary.md"]["content"].decode()
    assert (run.workspace / "documents" / "report.docx").is_file()
    svg_error = [e for e in run.events("validation_error") if e["errors"][0]["path"] == "content"]
    assert svg_error and "static <svg>" in svg_error[0]["errors"][0]["message"]
    artifact_events = {e["name"] for e in run.events("artifact")}
    assert artifact_events == set(by_name)


def test_code_tool_runs_python_without_network(harness: Harness) -> None:
    harness.llm.add_rules(
        {
            "responses": [
                tool_call(
                    "code",
                    {
                        "code": "import json, pathlib\npathlib.Path('out.json').write_text(json.dumps({'n': sum(range(10))}))\nprint('total', sum(range(10)))"
                    },
                ),
                tool_call("code", {"code": "import socket\nsocket.create_connection(('example.com', 80))"}),
                finish(),
            ]
        }
    )
    run = harness.run(harness.spec(output=None, tools=ALL_LOCAL))
    assert run.completion["status"] == "succeeded"
    assert json.loads((run.workspace / "out.json").read_text()) == {"n": 45}
    results = run.events("tool_result")
    assert results[0]["ok"] is True and results[1]["ok"] is False
    assert [d["rule"] for d in run.events("denied")] == ["sandbox_network"]
    assert "total 45" in json.dumps(harness.llm.requests()[1]["body"]["messages"][-1])


def test_terminal_has_no_credentials_in_its_environment(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Z_JOB_TOKEN", "zjb-should-never-be-visible")
    monkeypatch.setenv("SOME_API_TOKEN", "tok-should-never-be-visible")
    harness.llm.add_rules({"responses": [tool_call("terminal", {"command": "env | sort"}), finish()]})
    run = harness.run(harness.spec(output=None, tools=ALL_LOCAL))
    output = run.events("terminal")[0]["output_tail"]
    assert "should-never-be-visible" not in output and "Z_" not in output
    assert "HOME=" in output


def test_terminal_not_offered_without_a_workspace(harness: Harness) -> None:
    harness.llm.add_rules({"responses": [finish("nothing")]})
    spec = harness.spec(output=None, tools=ALL_LOCAL, workspace={"mode": "none"})
    run = harness.run(spec)
    names = {t["function"]["name"] for t in harness.llm.requests()[0]["body"]["tools"]}
    assert "terminal" not in names and {"read", "code", "documents"} <= names
    assert run.completion["status"] == "succeeded"


def test_search_memory_and_spawn_subagent(harness: Harness) -> None:
    harness.llm.add_rules(
        {
            "responses": [
                tool_call("search_memory", {"query": "release notes", "top_k": 3}),
                tool_call(
                    "spawn_subagent", {"agent": "summarizer", "prompt": "Summarize notes.md", "budget_share": 0.5}
                ),
                finish(),
            ]
        }
    )
    spec = harness.spec(output=None, tools={"local": ["read"], "builtins": ["spawn_subagent"], "gateway": []})
    job = harness.add(spec)
    job.memory = [{"id": "mem-7", "scope": "team", "text": "Payments releases ship on Tuesdays."}]
    run = harness.run(spec, job)
    assert run.completion["status"] == "succeeded"
    subagent = run.events("subagent")[0]
    assert subagent["agent"] == "summarizer" and subagent["child_job_id"].startswith("job-child-")
    messages = json.dumps([r["body"]["messages"][-1] for r in harness.llm.requests()])
    assert "Payments releases ship on Tuesdays." in messages
    assert '\\"answer\\": 42' in messages
    assert [r["ok"] for r in run.events("tool_result")] == [True, True]


def _git_repo(root: Path, pwned: Path) -> bytes:
    """A repository whose own config tries to run commands whenever git touches it (untrusted input)."""
    root.mkdir()
    (root / "README.md").write_text("# Payments\nversion: 1.0\n")
    (root / ".gitattributes").write_text("*.md filter=evil diff=evil\n")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "PATH": "/usr/bin:/bin",
    }
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True)
    with (root / ".git" / "config").open("a") as config:
        config.write(
            f'[core]\n\tfsmonitor = "touch {pwned}"\n'
            f'[filter "evil"]\n\tclean = "touch {pwned}; cat"\n\tsmudge = cat\n'
            f'[diff "evil"]\n\ttextconv = "touch {pwned}; cat"\n'
        )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(root.rglob("*")):
            tar.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
    return buf.getvalue()


def test_git_clone_workspace_yields_a_patch_artifact(harness: Harness, tmp_path: Path) -> None:
    pwned = tmp_path / "pwned"
    archive = _git_repo(tmp_path / "repo", pwned)
    harness.llm.add_rules(
        {
            "responses": [
                tool_call(
                    "edit",
                    {"command": "replace", "path": "README.md", "old_text": "version: 1.0", "new_text": "version: 1.1"},
                ),
                tool_call("edit", {"command": "write", "path": "CHANGELOG.md", "content": "1.1: bump\n"}),
                tool_call("edit", {"command": "write", "path": "report.md", "content": "# Report\nBumped.\n"}),
                finish(),
            ]
        }
    )
    spec = harness.spec(output=None, tools=ALL_LOCAL, workspace={"mode": "git_clone", "archive_url": "x"})
    job = harness.server.add_job(
        spec, archive=archive, manifest=make_manifest({"README.md": "# Payments\nversion: 1.0\n"})
    )
    run = harness.run(spec, job)
    assert run.completion["status"] == "succeeded"
    patch = next(a for a in run.job.artifacts if a["kind"] == "patch")
    text = patch["content"].decode()
    assert "-version: 1.0" in text and "+version: 1.1" in text and "CHANGELOG.md" in text
    assert "report.md" not in text
    report = next(a for a in run.job.artifacts if a["kind"] == "report")
    assert run.completion["report_artifact"] == report["id"]
    edits = run.events("file_edit")
    assert edits[0] == {"path": "README.md", "added": 1, "removed": 1}
    assert not pwned.exists(), "the repository's own config ran a command"
    # the patch applies on a clean copy of the staged tree
    check = tmp_path / "check"
    check.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        tar.extractall(check, filter="data")
    shutil.rmtree(check / ".git")
    (tmp_path / "changes.patch").write_bytes(patch["content"])
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    subprocess.run(["git", "apply", str(tmp_path / "changes.patch")], cwd=check, env=env, check=True)
    assert (check / "README.md").read_text() == "# Payments\nversion: 1.1\n"
    assert (check / "CHANGELOG.md").read_text() == "1.1: bump\n"


def test_sigterm_stops_after_the_step_without_completing(harness: Harness, tmp_path: Path) -> None:
    harness.llm.add_rules(
        {"responses": [tool_call("read", {"path": "notes.md"}), tool_call("read", {"path": "."})], "repeat": True}
    )
    spec = harness.spec()
    job = harness.add(spec)
    runner = Runner(
        harness.server.url,
        spec.job_id,
        job.bootstrap_token,
        RunnerConfig(workspace_dir=tmp_path / "ws", state_dir=tmp_path / "st", strict_events=True),
    )

    def terminate(job_: FakeJob, record: dict) -> None:
        if record["step"] == 1:
            runner.request_termination()

    job.on_checkpoint = terminate
    assert runner.run() == EXIT_INTERRUPTED
    assert job.completion is None
    assert [c["step"] for c in job.checkpoints] == [1]
    harness.server.assert_clean()
