"""Platform guardrails enforced inside the runner (docs/04 §4 and §5). Packs, playbooks and prompts cannot change them.

* `check_command`: network-block *feedback* for the sandboxed shell. Real enforcement is the NetworkPolicy /
  Docker network and the egress proxy; this filter refuses obvious network commands with a clear message and a
  `denied` event so the agent stops trying.
* `LoopDetector`: same tool + same arguments N times in a row, or no progress for M steps.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

SANDBOX_NETWORK = "sandbox_network"

NETWORK_BINARIES = frozenset(
    {
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "ssh",
        "scp",
        "sftp",
        "telnet",
        "ftp",
        "tftp",
        "rsync",
        "nmap",
        "aria2c",
        "lynx",
        "links",
        "w3m",
        "httpie",
        "http",
        "https",
        "xh",
        "ssh-keyscan",
    }
)
# Wrappers whose arguments start another command.
_WRAPPERS = frozenset(
    {
        "sudo",
        "env",
        "nohup",
        "nice",
        "time",
        "timeout",
        "xargs",
        "exec",
        "command",
        "builtin",
        "stdbuf",
        "ionice",
        "setsid",
        "watch",
    }
)
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash", "busybox"})
_DEV_NET = re.compile(r"/dev/(tcp|udp)/")
_SEPARATORS = re.compile(r"\|\||&&|[|;&\n`]|\$\(|<\(|>\(|\(|\)|\{|\}")
_URL = re.compile(r"(?i)\b(?:https?|ftp|git|ssh|svn)://")
_PIP = frozenset({"pip", "pip3", "uv", "poetry", "pipx"})
_PIP_INDEX_FLAGS = (
    "--index-url",
    "-i",
    "--extra-index-url",
    "--trusted-host",
    "--find-links",
    "-f",
    "--index",
    "--default-index",
)
_GIT_REMOTE_VERBS = frozenset({"clone", "fetch", "pull", "push", "ls-remote"})
_GIT_REMOTE_SUBVERBS = frozenset(
    {("remote", "update"), ("submodule", "update"), ("submodule", "add"), ("submodule", "sync")}
)
_PKG_REGISTRY_FLAGS = ("--registry",)


@dataclass(frozen=True)
class Denial:
    what: str
    rule: str
    detail: str


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return segment.split()


def _strip_prefix(words: list[str]) -> list[str]:
    """Drop variable assignments and command wrappers to find the real command word."""
    i = 0
    while i < len(words):
        word = words[i]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
            i += 1
            continue
        base = word.rsplit("/", 1)[-1]
        if base in _WRAPPERS:
            i += 1
            # skip wrapper options and a numeric duration (timeout 10, nice -n 5)
            while i < len(words) and (words[i].startswith("-") or re.fullmatch(r"\d+[smhd]?", words[i])):
                i += 1
            continue
        break
    return words[i:]


def _check_words(words: list[str], depth: int) -> Denial | None:
    words = _strip_prefix(words)
    if not words:
        return None
    cmd = words[0].rsplit("/", 1)[-1]
    if cmd in NETWORK_BINARIES:
        return Denial(cmd, SANDBOX_NETWORK, f"`{cmd}` opens network connections, which the sandbox does not allow")
    if cmd in _SHELLS and depth < 3:
        for idx, word in enumerate(words[1:], start=1):
            if word in ("-c", "-lc", "-ic") and idx + 1 < len(words):
                return check_command(words[idx + 1], _depth=depth + 1)
    if cmd in _PIP or (cmd in ("python", "python3") and words[1:3] == ["-m", "pip"]):
        args = words[1:]
        if any(a == flag or a.startswith(flag + "=") for a in args for flag in _PIP_INDEX_FLAGS):
            return Denial(
                f"{cmd} with a custom index",
                SANDBOX_NETWORK,
                "package indexes other than the configured mirror are not reachable; drop --index-url/--extra-index-url",
            )
        if any(_URL.search(a) or a.startswith("git+") for a in args):
            return Denial(
                f"{cmd} from a URL", SANDBOX_NETWORK, "installing packages from a URL is not allowed; use the mirror"
            )
    if cmd in ("npm", "pnpm", "yarn") and any(
        a == f or a.startswith(f + "=") for a in words[1:] for f in _PKG_REGISTRY_FLAGS
    ):
        return Denial(
            f"{cmd} with a custom registry", SANDBOX_NETWORK, "only the configured package mirror is reachable"
        )
    if cmd == "openssl" and "s_client" in words[1:]:
        return Denial("openssl s_client", SANDBOX_NETWORK, "`openssl s_client` opens network connections")
    if cmd == "git" and len(words) > 1:
        verbs = [w for w in words[1:] if not w.startswith("-")]
        verb = verbs[0] if verbs else ""
        sub = verbs[1] if len(verbs) > 1 else ""
        if verb in _GIT_REMOTE_VERBS or (verb, sub) in _GIT_REMOTE_SUBVERBS:
            return Denial(
                f"git {verb}",
                SANDBOX_NETWORK,
                "git remotes are not reachable from the sandbox; the workspace is already staged, and changes leave "
                "through propose_action (for example a merge request), never through git push",
            )
    return None


def check_command(command: str, *, _depth: int = 0) -> Denial | None:
    """Return a Denial when a shell command obviously tries to reach the network, else None."""
    if _DEV_NET.search(command):
        return Denial("/dev/tcp", SANDBOX_NETWORK, "raw sockets through /dev/tcp or /dev/udp are not allowed")
    for segment in _SEPARATORS.split(command):
        segment = segment.strip()
        if not segment:
            continue
        denial = _check_words(_words(segment), _depth)
        if denial is not None:
            return denial
    return None


def network_refusal_message(denial: Denial) -> str:
    return (
        f"Refused by the platform guardrail `{denial.rule}`: {denial.detail}. The command was NOT run. "
        "The sandbox has no network access except the internal package mirror (already configured as the default "
        "index). Use the provided gateway tools to read external systems, and propose_action to change them."
    )


def canonical_args(args: Any) -> str:
    return json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)


@dataclass
class LoopDetector:
    """Detects the agent repeating itself. State is plain data so it can be checkpointed."""

    repeat_limit: int = 3
    no_progress_limit: int = 8
    last_signature: str | None = None
    repeat_count: int = 0
    steps_without_progress: int = 0
    seen: list[str] = field(default_factory=list)
    _progress_this_step: bool = False

    def record_call(self, tool: str, args: Any) -> None:
        signature = f"{tool}:{canonical_args(args)}"
        if signature == self.last_signature:
            self.repeat_count += 1
        else:
            self.last_signature = signature
            self.repeat_count = 1
        if signature not in self.seen:
            self.seen.append(signature)
            self.seen = self.seen[-500:]
            self._progress_this_step = True

    def record_progress(self) -> None:
        """Something new happened (a message, a file change, a new successful call)."""
        self._progress_this_step = True

    def end_step(self) -> str | None:
        """Call once per step. Returns a reason when a loop is detected."""
        if self._progress_this_step:
            self.steps_without_progress = 0
        else:
            self.steps_without_progress += 1
        self._progress_this_step = False
        if self.repeat_count >= self.repeat_limit:
            return f"the same tool call was repeated {self.repeat_count} times in a row"
        if self.steps_without_progress >= self.no_progress_limit:
            return f"no progress for {self.steps_without_progress} steps"
        return None

    def to_state(self) -> dict[str, Any]:
        return {
            "last_signature": self.last_signature,
            "repeat_count": self.repeat_count,
            "steps_without_progress": self.steps_without_progress,
            "seen": self.seen,
        }

    def load_state(self, data: dict[str, Any]) -> None:
        self.last_signature = data.get("last_signature")
        self.repeat_count = int(data.get("repeat_count", 0))
        self.steps_without_progress = int(data.get("steps_without_progress", 0))
        self.seen = list(data.get("seen", []))
