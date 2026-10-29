"""Invariant 1: no LLM call goes anywhere except the LiteLLM gateway, so no model provider URL appears in code.

Provider endpoints belong in LiteLLM's configuration (config/*.example.yaml) and in the documentation, never in
the source of Z's components, their images or the chart's templates. Tests may name a provider host (to prove it
is refused), so test files are not scanned."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CODE = [
    "server",
    "runner",
    "cli",
    "packages",
    "browser",
    "ui/src",
    "deploy/images",
    "deploy/helm/z/templates",
    "deploy/helm/z/files",
]
SKIP_DIRS = {"tests", "node_modules", "__pycache__", ".venv", "dist", "build", ".mypy_cache", ".ruff_cache"}
TEXT = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yaml", ".yml", ".toml", ".tpl", ".txt", ".conf", ".sh", ""}

PROVIDER_HOST = re.compile(
    r"""
    api\.openai\.com
    | [a-z0-9-]*\.?openai\.azure\.com
    | api\.anthropic\.com
    | generativelanguage\.googleapis\.com
    | aiplatform\.googleapis\.com
    | bedrock(-runtime|-agent-runtime)?(-fips)?\.[a-z0-9-]+\.amazonaws\.com
    | api\.mistral\.ai
    | api\.cohere\.(ai|com)
    | api\.groq\.com
    | api\.together\.(xyz|ai)
    | api\.deepseek\.com
    | api\.fireworks\.ai
    | api\.x\.ai
    | openrouter\.ai
    | api-inference\.huggingface\.co
    """,
    re.IGNORECASE | re.VERBOSE,
)


def code_files() -> list[Path]:
    out = []
    for top in CODE:
        base = ROOT / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            rel = path.relative_to(ROOT)
            if not path.is_file() or SKIP_DIRS & set(rel.parts[:-1]) or path.name.startswith("test_"):
                continue
            if path.suffix in TEXT and ".example." not in path.name:
                out.append(path)
    return out


@pytest.mark.parametrize(
    "text",
    [
        "https://api.openai.com/v1/chat/completions",
        "base_url='https://my-resource.openai.azure.com/'",
        "https://API.ANTHROPIC.COM/v1/messages",
        "generativelanguage.googleapis.com/v1beta",
        "https://bedrock-runtime.us-east-1.amazonaws.com/model/x/invoke",
        "https://api.mistral.ai/v1",
    ],
)
def test_the_pattern_finds_provider_hosts(text: str) -> None:
    assert PROVIDER_HOST.search(text)


def test_the_pattern_leaves_the_gateway_and_internal_hosts_alone() -> None:
    for text in ("http://litellm:4000/v1", "https://inferx.internal.example/v1", "openai/z-heavy", "anthropic"):
        assert not PROVIDER_HOST.search(text), text


def test_no_model_provider_url_in_code() -> None:
    files = code_files()
    assert len(files) > 100, "the scan found no source: the layout changed"
    hits = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if match := PROVIDER_HOST.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}: {match.group(0)}")
    assert not hits, "model provider URLs in code (only the LiteLLM gateway is reachable):\n" + "\n".join(hits)
