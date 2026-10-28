"""Secret redaction for events, artifacts, logs and traces (invariant 11, docs/04 §5).

Two layers: known secret values (exact match, e.g. the job's own tokens) and patterns for common credential
formats. Applied by the runner before anything leaves it and again by z-server on ingestion.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

MASK = "[REDACTED]"
MIN_KNOWN_LENGTH = 8

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pem_private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("authorization_header", re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("url_credentials", re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+):[^/\s@]+@")),
    ("gitlab_token", re.compile(r"\b(glpat|gldt|glrt|glptt|glsoat|glimt|glagent|gloas)-[A-Za-z0-9_-]{16,}")),
    ("github_token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("z_token", re.compile(r"\b(zs|zt|zw|zj|zr)_[A-Za-z0-9_-]{20,}")),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)(\s*[:=]\s*)(['\"]?)[^\s'\",;]{6,}\3"
        ),
    ),
]


class Redactor:
    def __init__(self, known: Iterable[str] = ()) -> None:
        self.known = sorted({k for k in known if k and len(k) >= MIN_KNOWN_LENGTH}, key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self.known:
            value = value.replace(secret, MASK)
        for name, pattern in PATTERNS:
            if name == "authorization_header":
                value = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)} {MASK}", value)
            elif name == "url_credentials":
                value = pattern.sub(lambda m: f"{m.group(1)}:{MASK}@", value)
            elif name == "secret_assignment":
                value = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}{m.group(3)}", value)
            else:
                value = pattern.sub(MASK, value)
        return value

    def value(self, data: Any) -> Any:
        """Redact every string inside a JSON-like value (keys included)."""
        if isinstance(data, str):
            return self.text(data)
        if isinstance(data, dict):
            return {self.text(str(k)): self.value(v) for k, v in data.items()}
        if isinstance(data, list | tuple):
            return [self.value(v) for v in data]
        return data


def redact(data: Any, known: Iterable[str] = ()) -> Any:
    return Redactor(known).value(data)
