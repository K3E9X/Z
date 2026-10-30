"""LiteLLM callback deploy/helm/z/files/z_inference_guard.py (ADR 0024): answers served through an inferX external
fallback are refused for C2/C3 keys, never retried, and always logged."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

PATH = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "z" / "files" / "z_inference_guard.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("z_inference_guard", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def response(headers: dict[str, str]) -> Any:
    return SimpleNamespace(_hidden_params={"additional_headers": headers})


def run(guard: Any, metadata: dict[str, Any] | None, headers: dict[str, str]) -> Any:
    key = SimpleNamespace(metadata=metadata)
    return asyncio.run(guard.async_post_call_success_hook({"model": "z-heavy"}, key, response(headers)))


def test_normal_answers_pass() -> None:
    guard = load().guard
    ok = response({"llm_provider-x-request-id": "r1"})
    key = SimpleNamespace(metadata={"data_class": "C3"})
    assert asyncio.run(guard.async_post_call_success_hook({}, key, ok)) is ok


@pytest.mark.parametrize("metadata", [{"data_class": "C2"}, {"data_class": "C3"}, {}, None])
def test_fallback_answers_are_refused_for_c2_and_above(metadata: dict[str, Any] | None) -> None:
    with pytest.raises(HTTPException) as refused:
        run(load().guard, metadata, {"llm_provider-x-inferx-fallback": "genapi"})
    assert refused.value.status_code == 403  # never 5xx: a retry would send the data out again
    assert "genapi" in str(refused.value.detail)


def test_fallback_answers_pass_for_c1_but_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="z.inference_guard"):
        out = run(load().guard, {"data_class": "C1", "mission": "msn_1"}, {"LLM_PROVIDER-X-INFERX-FALLBACK": "genapi"})
    assert out is not None
    assert "external fallback genapi" in caplog.text and "msn_1" in caplog.text


class Stream:
    """A LiteLLM stream: provider headers on the wrapper, then chunks."""

    def __init__(self, headers: dict[str, str]) -> None:
        self._hidden_params = {"additional_headers": headers}

    def __aiter__(self) -> Any:
        async def chunks() -> Any:
            for c in ("a", "b"):
                yield c

        return chunks()


def test_streamed_fallback_answers_are_refused_before_the_first_chunk() -> None:
    guard = load().guard

    async def consume(metadata: dict[str, Any], headers: dict[str, str]) -> list[str]:
        key = SimpleNamespace(metadata=metadata)
        return [c async for c in guard.async_post_call_streaming_iterator_hook(key, Stream(headers), {"model": "m"})]

    assert asyncio.run(consume({"data_class": "C3"}, {})) == ["a", "b"]
    with pytest.raises(HTTPException) as refused:
        asyncio.run(consume({"data_class": "C2"}, {"llm_provider-x-inferx-fallback": "genapi"}))
    assert refused.value.status_code == 403
    assert asyncio.run(consume({"data_class": "C1"}, {"llm_provider-x-inferx-fallback": "genapi"})) == ["a", "b"]
