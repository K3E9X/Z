"""Secret redaction (docs/04 §5, invariant 11).

Every string that leaves the runner (events, artifacts, logs, completion errors) and every tool result shown to
the model passes through a `Redactor`. Two mechanisms:

* **known secrets**: exact values the runner holds (job bootstrap token, session token, LLM keys, including
  rotated ones). They are replaced wherever they appear, longest first.
* **patterns**: common credential shapes (GitLab, OpenAI-style, GitHub, Slack and AWS keys, JWTs,
  `Authorization:` headers, credentials in URLs, PEM blocks) and secret-looking keys in structured data.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from typing import Any

MASK = "[REDACTED]"
MIN_KNOWN_SECRET_LEN = 8

_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]+-----.*?-----END [A-Z0-9 ]+-----", re.S)

# (name, pattern, replacement). Replacements keep a harmless prefix so the text stays readable.
_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("pem_block", _PEM, MASK),
    (
        "authorization_header",
        re.compile(r"(?i)\b((?:proxy-)?authorization\s*[:=]\s*)(?:(?:bearer|basic|token|apikey)\s+)?[^\s'\",;]+"),
        r"\1" + MASK,
    ),
    (
        "token_header",
        re.compile(r"(?i)\b((?:private-token|x-api-key|api-key|x-auth-token)\s*[:=]\s*)[^\s'\",;]+"),
        r"\1" + MASK,
    ),
    ("url_credentials", re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@'\"]+:[^/\s@'\"]+@"), r"\1" + MASK + "@"),
    ("gitlab_token", re.compile(r"\bgl(?:pat|oas|dt|rt|ptt|ft|imt|agent|wt)-[A-Za-z0-9_\-]{16,}"), MASK),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"), MASK),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), MASK),
    ("sk_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), MASK),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[A-Z0-9]{16}\b"), MASK),
    ("aws_secret_key", re.compile(r"(?i)(aws_secret_access_key\s*[=:]\s*['\"]?)[A-Za-z0-9/+=]{40}"), r"\1" + MASK),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MASK),
    (
        "password_assignment",
        re.compile(
            r"(?i)\b((?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)\s*[=:]\s*['\"]?)[^\s'\",;]{6,}"
        ),
        r"\1" + MASK,
    ),
)

_SECRET_KEY = re.compile(
    r"(?i)^(authorization|proxy-authorization|cookie|set-cookie|password|passwd|secret|client_secret|api[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|session[_-]?token|private[_-]?key|private-token|x-api-key|token)$"
)


class Redactor:
    """Thread-safe redactor. `add_secret` registers values the runner learns at run time (rotated keys)."""

    def __init__(self, secrets: Iterable[str | None] = ()) -> None:
        self._lock = threading.Lock()
        self._known: list[str] = []
        for secret in secrets:
            self.add_secret(secret)

    def add_secret(self, secret: str | None) -> None:
        if not secret or len(secret) < MIN_KNOWN_SECRET_LEN:
            return
        with self._lock:
            if secret not in self._known:
                self._known.append(secret)
                self._known.sort(key=len, reverse=True)

    @property
    def known_count(self) -> int:
        with self._lock:
            return len(self._known)

    def text(self, value: str) -> str:
        """Redact one string."""
        if not value:
            return value
        with self._lock:
            known = list(self._known)
        for secret in known:
            if secret in value:
                value = value.replace(secret, MASK)
        for _name, pattern, replacement in _PATTERNS:
            value = pattern.sub(replacement, value)
        return value

    def contains_known_secret(self, data: bytes) -> bool:
        """True when raw bytes contain a known secret (binary artifacts cannot be rewritten safely)."""
        with self._lock:
            known = list(self._known)
        return any(secret.encode("utf-8") in data for secret in known)

    def value(self, value: Any) -> Any:
        """Redact a JSON-like structure: strings are redacted, values under secret-looking keys are masked."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            out: dict[Any, Any] = {}
            for key, item in value.items():
                if isinstance(key, str) and _SECRET_KEY.match(key) and item not in (None, "", [], {}):
                    out[key] = MASK
                else:
                    out[key] = self.value(item)
            return out
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.value(item) for item in value)
        return value
