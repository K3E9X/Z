"""git over HTTPS for the clone helper (git_clone workspaces) and the merge request helper (executor).

The token never appears in argv, the URL or the repository config: it is passed as an `http.extraHeader`
through `GIT_CONFIG_COUNT/KEY/VALUE` environment variables of the git process only. No system or global
config, no hooks, no credential helpers, no submodules, only http(s) transports. The working copies live in
private temporary directories that are removed afterwards.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from z_common.redaction import Redactor

REPO_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)+$")
BRANCH = re.compile(r"^(?!.*\.\.)(?!/)[A-Za-z0-9._/-]{1,200}(?<![./])$")
MAX_PATCH_BYTES = 20 * 1024 * 1024
GIT_TIMEOUT_S = 180


class GitError(RuntimeError):
    pass


@dataclass
class Checkout:
    path: Path
    commit: str
    ref: str


def repo_url(base_url: str, repo: str) -> str:
    if not REPO_PATH.match(repo) or ".." in repo:
        raise GitError(f"invalid repository path {repo!r} (expected group/project)")
    parts = urlsplit(base_url)
    if parts.scheme not in {"https", "http"} or not parts.netloc:
        raise GitError("the connector base_url must be http(s)")
    return f"{base_url.rstrip('/')}/{repo}.git"


def _env(home: str, token: str | None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": home,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ASKPASS": "/bin/false",
        "GIT_ALLOW_PROTOCOL": "https:http",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    for name in (
        "SSL_CERT_FILE",
        "GIT_SSL_CAINFO",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    ):
        if os.environ.get(name):
            env[name] = os.environ[name]
    settings = [
        ("core.hooksPath", os.devnull),
        ("credential.helper", ""),
        ("submodule.recurse", "false"),
        ("protocol.file.allow", "never"),
    ]
    if token:
        basic = base64.b64encode(f"oauth2:{token}".encode()).decode()
        settings.append(("http.extraHeader", f"Authorization: Basic {basic}"))
    env["GIT_CONFIG_COUNT"] = str(len(settings))
    for i, (key, value) in enumerate(settings):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = key, value
    return env


async def _git(args: list[str], cwd: Path, env: dict[str, str], redactor: Redactor, stdin: bytes | None = None) -> str:
    binary = shutil.which("git")
    if binary is None:
        raise GitError("git is not installed in the z-server image")
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), GIT_TIMEOUT_S)
    except TimeoutError as exc:
        proc.kill()
        raise GitError(f"git {args[0]} timed out") from exc
    if proc.returncode != 0:
        message = redactor.text(err.decode(errors="replace").strip())[-800:]
        raise GitError(f"git {args[0]} failed: {message}")
    return out.decode(errors="replace").strip()


async def clone(base_url: str, repo: str, ref: str | None, token: str | None, depth: int = 1) -> Checkout:
    """Shallow clone into a private temporary directory (caller removes `Checkout.path.parent`)."""
    url = repo_url(base_url, repo)
    if ref is not None and not BRANCH.match(ref):
        raise GitError(f"invalid ref {ref!r}")
    root = Path(tempfile.mkdtemp(prefix="z-git-"))
    os.chmod(root, 0o700)
    env = _env(str(root), token)
    redactor = Redactor([token] if token else [])
    target = root / "repo"
    args = ["clone", "--quiet", "--depth", str(depth), "--no-tags", "--single-branch", "--no-recurse-submodules"]
    if ref:
        args += ["--branch", ref]
    try:
        await _git([*args, "--", url, str(target)], root, env, redactor)
        commit = await _git(["rev-parse", "HEAD"], target, env, redactor)
        branch = ref or await _git(["rev-parse", "--abbrev-ref", "HEAD"], target, env, redactor)
    except GitError:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return Checkout(target, commit, branch)


def check_patch(patch: bytes) -> None:
    if not patch.strip():
        raise GitError("the patch is empty")
    if len(patch) > MAX_PATCH_BYTES:
        raise GitError("the patch is larger than 20 MB")
    for line in patch.decode(errors="replace").splitlines():
        if line.startswith(("+++ ", "--- ", "diff --git ", "rename to ", "rename from ", "copy to ")):
            paths = line.split()[1:] if line.startswith("diff --git") else [line.split(" ", 2)[-1]]
            for path in paths:
                clean = path[2:] if path[:2] in {"a/", "b/"} else path
                if clean != "/dev/null" and (
                    clean.startswith("/") or ".." in Path(clean).parts or Path(clean).parts[:1] == (".git",)
                ):
                    raise GitError(f"the patch touches a forbidden path: {clean}")
        if line.startswith("new file mode 120000") or line.startswith("new mode 120000"):
            raise GitError("the patch creates a symbolic link")


async def push_patch(
    base_url: str,
    repo: str,
    target_branch: str,
    source_branch: str,
    patch: bytes,
    message: str,
    token: str,
    author: tuple[str, str],
) -> str:
    """Apply `patch` on `target_branch` and push it as `source_branch` (z/* only). Returns the commit sha."""
    if not source_branch.startswith("z/") or not BRANCH.match(source_branch):
        raise GitError("merge request branches must be z/<name>")
    check_patch(patch)
    checkout = await clone(base_url, repo, target_branch, token)
    env = _env(str(checkout.path.parent), token)
    env.update(
        {
            "GIT_AUTHOR_NAME": author[0],
            "GIT_AUTHOR_EMAIL": author[1],
            "GIT_COMMITTER_NAME": author[0],
            "GIT_COMMITTER_EMAIL": author[1],
        }
    )
    redactor = Redactor([token])
    try:
        await _git(["apply", "--check", "--whitespace=nowarn", "-"], checkout.path, env, redactor, stdin=patch)
        await _git(["apply", "--index", "--whitespace=nowarn", "-"], checkout.path, env, redactor, stdin=patch)
        await _git(["commit", "--quiet", "--no-verify", "-m", message], checkout.path, env, redactor)
        sha = await _git(["rev-parse", "HEAD"], checkout.path, env, redactor)
        await _git(
            ["push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{source_branch}"], checkout.path, env, redactor
        )
        return sha
    finally:
        shutil.rmtree(checkout.path.parent, ignore_errors=True)
