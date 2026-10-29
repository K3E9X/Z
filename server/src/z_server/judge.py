"""Injection scan, deterministic stage (docs/04 §6, invariant 6).

Every tool result and staged workspace input goes through `scan()` before it reaches an agent. This stage
looks for the shapes injection attacks need (instruction overrides, fake role or tool markup, exfiltration
requests, hidden text) and is cheap enough to run on everything. The model stage (`z-judge` through
LiteLLM, B6) refines verdicts on what this stage flags or samples; it never lowers a `blocked` verdict.

- `flagged`: content is delivered wrapped and labelled untrusted, the timeline shows `judge: flagged`.
- `blocked`: several independent strong signals; the content is withheld and the job needs attention.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from z_common.models.enums import DataClass, JudgeVerdict

SAMPLE_HEAD = 256 * 1024
SAMPLE_TAIL = 64 * 1024


@dataclass(frozen=True)
class Signal:
    category: str
    strong: bool
    pattern: re.Pattern[str]


def _p(expr: str) -> re.Pattern[str]:
    return re.compile(expr, re.IGNORECASE | re.DOTALL)


QUALIFIED = r"(?:previous|prior|above|earlier|preceding|original|initial)"
TARGET = r"(?:instructions?|prompts?|rules|guidelines|directives|guardrails|context)"
SENSITIVE = r"(?:tokens?|secrets?|passwords?|credentials?|api[_ -]?keys?|\$\(env\)|\$\{?[A-Z_]*(?:TOKEN|KEY|SECRET|PASSWORD)\}?)"

SIGNALS: tuple[Signal, ...] = (
    # "ignore all previous instructions", "disregard the above guidelines": the qualifier is what separates an
    # override from prose that only talks about prompts ("attackers override the system prompt").
    Signal(
        "instruction_override",
        True,
        _p(
            rf"\b(?:ignore|disregard|forget|override|bypass)\s+(?:(?:all|any|the|your|my|of|these|those)\s+)*{QUALIFIED}\s+{TARGET}"
        ),
    ),
    Signal("instruction_override", True, _p(r"\bnew (?:system )?(?:instructions?|rules|directives)\s*:")),
    Signal(
        "instruction_override",
        True,
        _p(
            r"\b(?:disregard|ignore|abandon|forget)\s+(?:your|the|this|current)\s+(?:task|job|assignment|mission|goal)\b|\bstop (?:summari[sz]ing|your task|the task|what you are doing)\b"
        ),
    ),
    # content that talks to the model instead of to a human reader
    Signal(
        "agent_addressing",
        False,
        _p(
            r"\b(?:note|notice|message|instructions?)\s+(?:to|for)\s+(?:all\s+)?(?:automated |ai |llm )?(?:assistants?|agents?|models?|llms?|bots?)\b|(?:^|<!--|\n)\s*(?:ai|llm|automated) (?:assistant|agent)s?\s*:"
        ),
    ),
    Signal(
        "hidden_markup",
        False,
        _p(r"<!--(?:(?!-->).){0,600}?\b(?:assistant|agent|ai|llm|model)s?\b(?:(?!-->).){0,600}?-->"),
    ),
    Signal(
        "role_hijack",
        False,
        _p(
            r"\byou are (?:now|no longer)\b|\bfrom now on,? you (?:are|will|must)\b|\bact as (?:an? |the )?(?:admin|administrator|root|developer mode|dan)\b"
        ),
    ),
    Signal(
        "role_markup",
        True,
        _p(
            r"<\|(?:im_start|im_end|system|assistant|user|endoftext)\|>|\[/?INST\]|<</?SYS>>|</?(?:system|tool_result|function_results?)>"
        ),
    ),
    Signal("role_markup", False, _p(r"^\s*#{1,4}\s*(?:system|assistant)\s*(?:prompt|message)?\s*:?\s*$")),
    Signal(
        "prompt_leak",
        False,
        _p(
            r"\b(?:reveal|print|show|repeat|output|include|containing|copy|paste|send|write)\b.{0,30}\b(?:system prompt|your instructions|hidden instructions|initial prompt)\b"
        ),
    ),
    Signal(
        "credential_access",
        False,
        _p(r"~/\.(?:git-credentials|ssh/id_|aws/credentials|netrc|docker/config\.json|kube/config)|/etc/shadow\b"),
    ),
    Signal(
        "credential_request",
        False,
        _p(
            r"\b(?:all|every|any)\s+(?:the\s+)?(?:tokens?|secrets?|credentials?|passwords?|api keys?)\s+(?:that\s+)?you\s+(?:can\s+)?(?:see|find|access|have|know)\b"
        ),
    ),
    Signal(
        "tool_coercion",
        True,
        _p(
            r"\b(?:call|invoke|use|run)\b.{0,20}\b(?:propose_action|spawn_subagent|the [a-z_]+ tool)\b.{0,80}\b(?:without|do not|don't)\b.{0,20}\b(?:ask|approval|confirm|tell)"
        ),
    ),
    Signal(
        "concealment",
        False,
        _p(
            r"\b(?:do not|don't|never)\b.{0,20}\b(?:tell|inform|mention|reveal)\b.{0,20}\b(?:the )?(?:user|human|operator|reviewer|note|this)\b"
        ),
    ),
    # exfiltration needs a network tool or a URL next to a secret, not just the two words in a sentence
    Signal("exfiltration", True, _p(rf"\b(?:curl|wget|netcat|nc|invoke-webrequest)\b[^\n]{{0,160}}{SENSITIVE}")),
    Signal(
        "exfiltration",
        True,
        _p(rf"\b(?:send|post|upload|exfiltrate|forward)\b[^\n]{{0,80}}{SENSITIVE}[^\n]{{0,80}}https?://"),
    ),
    Signal("exfiltration", False, _p(r"!\[[^\]]*\]\(https?://[^)]*[?&][a-z_]+=\{?")),
)
HIDDEN = re.compile("[​-‏‪-‮⁠-⁤﻿]|[\U000e0000-\U000e007f]")


@dataclass
class Verdict:
    verdict: JudgeVerdict
    categories: list[str] = field(default_factory=list)
    reason: str | None = None


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", HIDDEN.sub("", text))


def sample(text: str) -> str:
    if len(text) <= SAMPLE_HEAD + SAMPLE_TAIL:
        return text
    return text[:SAMPLE_HEAD] + "\n" + text[-SAMPLE_TAIL:]


def scan(text: str) -> Verdict:
    hidden = len(HIDDEN.findall(text))
    body = _normalize(sample(text))
    strong: set[str] = set()
    weak: set[str] = set()
    for signal in SIGNALS:
        if signal.pattern.search(body):
            (strong if signal.strong else weak).add(signal.category)
    if hidden >= 8:
        weak.add("hidden_text")
    categories = sorted(strong | weak)
    if not categories:
        return Verdict(JudgeVerdict.clean)
    if len(strong) >= 2 or (strong and len(weak) >= 2):
        return Verdict(JudgeVerdict.blocked, categories, "several independent injection signals")
    return Verdict(JudgeVerdict.flagged, categories, "content matches injection patterns; treat as data")


def is_text(data: bytes, content_type: str | None = None) -> bool:
    if content_type and (
        content_type.startswith("text/")
        or content_type.split(";")[0]
        in {"application/json", "application/xml", "application/yaml", "application/x-yaml", "application/javascript"}
    ):
        return True
    head = data[:8192]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.start >= len(head) - 4  # a multi-byte char cut at the sample boundary
    return True


def scan_bytes(data: bytes, content_type: str | None = None) -> Verdict:
    """Binary files cannot carry instructions to the model directly; text and text-like files are scanned."""
    if not is_text(data, content_type):
        return Verdict(JudgeVerdict.clean)
    return scan(data.decode("utf-8", errors="replace"))


# Data classification of staged inputs (docs/04 §5, docs/06 §2): credentials in a file make the mission C3
# (restricted: secrets, credentials). Only formats that are credentials by construction count; a `password: …`
# line in an example config does not, so documentation does not lock every mission to C3.
CREDENTIAL_PATTERNS = frozenset(
    {
        "pem_private_key",
        "url_credentials",
        "gitlab_token",
        "github_token",
        "slack_token",
        "openai_style_key",
        "inferx_key",
        "aws_access_key",
        "jwt",
        "z_token",
    }
)


def classify(text: str) -> tuple[DataClass | None, list[str]]:
    """(C3, the credential kinds found) when the text holds credentials, else (None, [])."""
    from z_common.redaction import PATTERNS

    found = [name for name, pattern in PATTERNS if name in CREDENTIAL_PATTERNS and pattern.search(text)]
    return (DataClass.C3, found) if found else (None, [])
