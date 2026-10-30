from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from tests.fakes.fake_tools import FakeTools

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI not installed")


def git(*args: str, cwd: Path | None = None, home: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_GLOBAL": str(home / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(home),
    }
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, timeout=60, check=False)


def test_clone_push_and_merge_request(tools: FakeTools, tmp_path: Path) -> None:
    tools.reset()
    (tmp_path / "gitconfig").write_text("[user]\n\tname = Z Test\n\temail = z@example.test\n")
    host = tools.url.removeprefix("http://")
    repo = "gitlab/infra/payments-api.git"
    ro = f"http://oauth2:{tools.token('gitlab')}@{host}/{repo}"
    rw = f"http://oauth2:{tools.token('gitlab', 'write')}@{host}/{repo}"

    assert git("clone", f"http://{host}/{repo}", str(tmp_path / "anon"), home=tmp_path).returncode != 0
    work = tmp_path / "work"
    cloned = git("clone", ro, str(work), home=tmp_path)
    assert cloned.returncode == 0, cloned.stderr
    assert subprocess.run(["sh", "test.sh"], cwd=work, capture_output=True, check=False).returncode == 1

    git("checkout", "-b", "z/log4j-2.24.3", cwd=work, home=tmp_path)
    pom = work / "pom.xml"
    pom.write_text(pom.read_text().replace("2.14.1", "2.24.3"))
    assert git("commit", "-am", "Upgrade log4j-core to 2.24.3", cwd=work, home=tmp_path).returncode == 0
    assert subprocess.run(["sh", "test.sh"], cwd=work, capture_output=True, check=False).returncode == 0

    denied = git("push", "origin", "z/log4j-2.24.3", cwd=work, home=tmp_path)
    assert denied.returncode != 0 and "403" in denied.stderr
    pushed = git("push", rw, "z/log4j-2.24.3", cwd=work, home=tmp_path)
    assert pushed.returncode == 0, pushed.stderr

    push = tools.writes("gitlab.push")
    assert push[0]["token_kind"] == "write" and push[0]["data"]["updates"][0]["ref"] == "refs/heads/z/log4j-2.24.3"
    api = f"{tools.url}/gitlab/api/v4/projects/infra%2Fpayments-api"
    mr = httpx.post(
        f"{api}/merge_requests",
        headers={"PRIVATE-TOKEN": tools.token("gitlab", "write")},
        json={"source_branch": "z/log4j-2.24.3", "target_branch": "main", "title": "Upgrade log4j"},
    )
    assert mr.status_code == 201
    assert mr.json()["sha"] == push[0]["data"]["updates"][0]["new"]
    diffs = httpx.get(
        f"{api}/merge_requests/{mr.json()['iid']}/diffs", headers={"PRIVATE-TOKEN": tools.token("gitlab")}
    ).json()
    assert "+      <version>2.24.3</version>" in diffs[0]["diff"]

    # Fetch with the read token sees the pushed branch.
    fetched = git("ls-remote", ro, cwd=work, home=tmp_path)
    assert "refs/heads/z/log4j-2.24.3" in fetched.stdout
