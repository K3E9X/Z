from __future__ import annotations

import pytest

from z_common.redaction import MASK, redact


@pytest.mark.parametrize(
    ("raw", "must_vanish"),
    [
        ("token glpat-AbCdEfGhIjKlMnOpQrSt12", "glpat-AbCdEfGhIjKlMnOpQrSt12"),
        ("Authorization: Bearer abcdefghijklmnop.qrstu", "abcdefghijklmnop.qrstu"),
        ("curl https://bot:s3cr3tP4ss@gitlab.example.test/x.git", "s3cr3tP4ss"),
        ("key sk-proj-1234567890abcdefghij", "sk-proj-1234567890abcdefghij"),
        ("AKIAIOSFODNN7EXAMPLE in env", "AKIAIOSFODNN7EXAMPLE"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "eyJhbGciOiJIUzI1NiJ9"),
        ("password = 'hunter2hunter2'", "hunter2hunter2"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "MIIEow"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
        ("session zs_abcdefghijklmnopqrstuvwxyz", "zs_abcdefghijklmnopqrstuvwxyz"),
    ],
)
def test_patterns(raw: str, must_vanish: str) -> None:
    out = redact(raw)
    assert must_vanish not in out and MASK in out


def test_known_secrets_and_nesting() -> None:
    data = {"args": {"cmd": "echo my-job-token-value", "list": ["my-job-token-value"]}, "n": 3}
    out = redact(data, ["my-job-token-value", "short"])
    assert out == {"args": {"cmd": f"echo {MASK}", "list": [MASK]}, "n": 3}


def test_ordinary_text_is_untouched() -> None:
    text = "Upgrade log4j-core to 2.24.3 on bo-pay-03; CVSS 10.0; see CVE-2021-44228."
    assert redact(text) == text
