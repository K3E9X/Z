"""The YAML subset loader reads what security/*.yaml use and refuses everything else."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from simple_yaml import YamlSubsetError, load

ROOT = Path(__file__).resolve().parents[2]


def _as_text(value: object) -> object:
    """PyYAML converts dates and numbers; the subset loader keeps text. Compare on text."""
    if isinstance(value, dict):
        return {str(k): _as_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_as_text(v) for v in value]
    return None if value is None else str(value)


@pytest.mark.parametrize(
    "path",
    ["security/blocked-versions.yaml", "security/exceptions.yaml", "ci/security-exceptions.example.yaml"],
)
def test_repository_files_match_pyyaml(path: str) -> None:
    text = (ROOT / path).read_text()
    assert load(text) == _as_text(yaml.safe_load(text))


def test_block_and_flow_forms() -> None:
    text = """
---
# comment
exceptions:
  - id: CVE-2026-1
    scope: "image:z-ui"   # trailing comment
    reason: 'It''s not reachable # not a comment'
    owner: "@team"
    expires: 2026-12-31
  - {id: GHSA-x, scope: deps:npm, reason: r, owner: '@a', expires: 2026-11-01}
blocked:
- {ecosystem: pypi, name: litellm, versions: ["1.82.7", '1.82.8'], reason: "Backdoored, March 2026"}
nested:
  inner:
    - a
    -
      - b
  empty:
  list: []
  map: {}
"""
    assert load(text) == {
        "exceptions": [
            {
                "id": "CVE-2026-1",
                "scope": "image:z-ui",
                "reason": "It's not reachable # not a comment",
                "owner": "@team",
                "expires": "2026-12-31",
            },
            {"id": "GHSA-x", "scope": "deps:npm", "reason": "r", "owner": "@a", "expires": "2026-11-01"},
        ],
        "blocked": [
            {
                "ecosystem": "pypi",
                "name": "litellm",
                "versions": ["1.82.7", "1.82.8"],
                "reason": "Backdoored, March 2026",
            }
        ],
        "nested": {"inner": ["a", ["b"]], "empty": None, "list": [], "map": {}},
    }


def test_scalars_stay_text() -> None:
    assert load("a: 1.10\nb: 2026-01-01\nc: true\nd: ~\ne: 'x: y'\n") == {
        "a": "1.10",
        "b": "2026-01-01",
        "c": "true",
        "d": None,
        "e": "x: y",
    }


def test_empty_document() -> None:
    assert load("# nothing\n\n") is None


@pytest.mark.parametrize(
    "text",
    [
        "a: &anchor 1\n",
        "a: *alias\n",
        "a: !tag x\n",
        "a: |\n  block\n",
        "a: >\n  folded\n",
        "a: [1,\n  2]\n",
        "a: {b: 1\n",
        "a: 1\na: 2\n",
        "a: {b: 1, b: 2}\n",
        "a:\n\t- 1\n",
        "a: 1\n   b: 2\n",
        "a: 'unterminated\n",
        "a: b: c\n",
        "a: 1\n---\nb: 2\n",
        "- a\nb: 1\n",
    ],
)
def test_refuses_what_it_does_not_understand(text: str) -> None:
    with pytest.raises(YamlSubsetError):
        load(text)
