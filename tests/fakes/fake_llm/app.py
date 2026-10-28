"""Scriptable OpenAI-compatible server (chat completions, streaming, embeddings) for tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import random
import struct
import threading
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from .rules import RuleBook, Step, content_text, load_rules_file, parse_rules

DEFAULT_MODELS = ["z-heavy", "z-fast", "z-judge", "z-embed"]


def count_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def embed(text: str, dim: int) -> list[float]:
    """Deterministic unit vector from a hash of the text."""
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeLLMState:
    def __init__(self, embed_dim: int) -> None:
        self.rules = RuleBook()
        self.embed_dim = embed_dim
        self._lock = threading.Lock()
        self._requests: list[dict[str, Any]] = []

    def log(self, request: Request, body: dict[str, Any], rule_id: str | None = None) -> None:
        entry = {
            "ts": time.time(),
            "path": request.url.path,
            "model": body.get("model"),
            "headers": dict(request.headers),
            "body": body,
            "rule_id": rule_id,
        }
        with self._lock:
            self._requests.append(entry)

    def requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._requests)

    def clear_requests(self) -> None:
        with self._lock:
            self._requests.clear()


def _error_response(status: int, message: str, err_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": err_type, "code": status}}, status_code=status)


def _tool_calls(step: Step) -> list[dict[str, Any]]:
    calls = []
    for spec in step.tool_calls or []:
        args = spec.arguments if isinstance(spec.arguments, str) else json.dumps(spec.arguments)
        calls.append(
            {
                "id": spec.id or f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": spec.name, "arguments": args},
            }
        )
    return calls


def _content(step: Step) -> str | None:
    if step.json_ is not None:
        return json.dumps(step.json_)
    if step.text is not None:
        return step.text
    return None if step.tool_calls else ""


async def _resolve(step: Step) -> Step:
    """Apply delays and unwrap `then`."""
    while True:
        if step.delay_ms:
            await asyncio.sleep(step.delay_ms / 1000)
        if step.then is None:
            break
        step = step.then
    if step.text is None and step.tool_calls is None and step.json_ is None and step.error is None:
        return Step(text="ok")
    return step


def _sse(data: dict[str, Any] | str) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"data: {payload}\n\n".encode()


def _chunks(text: str, size: int = 16) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def create_app(rules_file: str | None = None, embed_dim: int | None = None) -> FastAPI:
    dim = embed_dim or int(os.environ.get("FAKE_LLM_EMBED_DIM", "1024"))
    state = FakeLLMState(dim)
    rules_path = rules_file or os.environ.get("FAKE_LLM_RULES")
    if rules_path:
        state.rules.add(load_rules_file(rules_path))

    app = FastAPI(title="fake-llm")
    app.state.fake = state

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        seen = list(DEFAULT_MODELS)
        for entry in state.requests():
            if entry["model"] and entry["model"] not in seen:
                seen.append(entry["model"])
        return {
            "object": "list",
            "data": [{"id": m, "object": "model", "created": 1700000000, "owned_by": "fake"} for m in seen],
        }

    @app.post("/v1/chat/completions", response_model=None)
    async def chat(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _error_response(400, "invalid JSON body")
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
            return _error_response(400, "'messages' is required")
        rule_id, step = state.rules.next_step(body)
        state.log(request, body, rule_id)
        step = await _resolve(step)
        if step.error is not None:
            return _error_response(step.error.status, step.error.message, step.error.type)

        model = str(body.get("model", "fake"))
        prompt_text = "\n".join(content_text(m.get("content")) for m in body["messages"])
        if body.get("tools"):
            prompt_text += json.dumps(body["tools"])
        content = _content(step)
        calls = _tool_calls(step)
        completion_text = (content or "") + "".join(c["function"]["name"] + c["function"]["arguments"] for c in calls)
        usage = {
            "prompt_tokens": count_tokens(prompt_text),
            "completion_tokens": count_tokens(completion_text),
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        finish = step.finish_reason or ("tool_calls" if calls else "stop")
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        if not body.get("stream"):
            message: dict[str, Any] = {"role": "assistant", "content": content}
            if calls:
                message["tool_calls"] = calls
            return JSONResponse(
                {
                    "id": cid,
                    "object": "chat.completion",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "message": message, "finish_reason": finish, "logprobs": None}],
                    "usage": usage,
                }
            )

        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

        def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> bytes:
            return _sse(
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason, "logprobs": None}],
                }
            )

        async def stream() -> AsyncIterator[bytes]:
            yield chunk({"role": "assistant", "content": ""})
            if content:
                for piece in _chunks(content):
                    yield chunk({"content": piece})
            for index, call in enumerate(calls):
                head = {"index": index, "id": call["id"], "type": "function"}
                yield chunk({"tool_calls": [{**head, "function": {"name": call["function"]["name"], "arguments": ""}}]})
                for piece in _chunks(call["function"]["arguments"]):
                    yield chunk({"tool_calls": [{"index": index, "function": {"arguments": piece}}]})
            yield chunk({}, finish)
            if include_usage:
                yield _sse(
                    {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [],
                        "usage": usage,
                    }
                )
            yield _sse("[DONE]")

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/v1/embeddings", response_model=None)
    async def embeddings(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _error_response(400, "invalid JSON body")
        state.log(request, body)
        raw = body.get("input")
        if raw is None:
            return _error_response(400, "'input' is required")
        items = raw if isinstance(raw, list) and raw and not isinstance(raw[0], int) else [raw]
        texts = [x if isinstance(x, str) else json.dumps(x) for x in items]
        dim = int(body.get("dimensions") or state.embed_dim)
        b64 = body.get("encoding_format") == "base64"
        data = []
        for i, text in enumerate(texts):
            vec = embed(text, dim)
            value: Any = base64.b64encode(struct.pack(f"<{dim}f", *vec)).decode() if b64 else vec
            data.append({"object": "embedding", "index": i, "embedding": value})
        tokens = sum(count_tokens(t) for t in texts)
        return JSONResponse(
            {
                "object": "list",
                "data": data,
                "model": body.get("model", "fake-embed"),
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
            }
        )

    # ---- control API -------------------------------------------------------
    @app.post("/_fake/rules")
    async def add_rules(request: Request) -> Response:
        try:
            rules = parse_rules(await request.json())
        except (ValueError, ValidationError) as exc:
            return _error_response(422, str(exc))
        return JSONResponse({"ids": state.rules.add(rules)})

    @app.get("/_fake/rules")
    def list_rules() -> list[dict[str, Any]]:
        return state.rules.dump()

    @app.delete("/_fake/rules")
    def clear_rules() -> dict[str, bool]:
        state.rules.clear()
        return {"ok": True}

    @app.get("/_fake/requests")
    def list_requests() -> list[dict[str, Any]]:
        return state.requests()

    @app.delete("/_fake/requests")
    def clear_requests() -> dict[str, bool]:
        state.clear_requests()
        return {"ok": True}

    @app.post("/_fake/reset")
    def reset() -> dict[str, bool]:
        state.rules.clear()
        state.clear_requests()
        return {"ok": True}

    return app
