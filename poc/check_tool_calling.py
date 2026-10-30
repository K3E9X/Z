"""Check that a model on the inference cluster handles tool calling well enough for Z.

Usage:
    uv run --with openai poc/check_tool_calling.py --base-url URL --api-key KEY --model MODEL_ID

Three tests: single tool call, multi-turn tool calls, structured JSON output.
Exit code 0 if all pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys

from openai import OpenAI

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the repository and return its content.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative file path"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List the files in a directory.",
            "parameters": {
                "type": "object",
                "properties": {"directory": {"type": "string"}},
                "required": ["directory"],
            },
        },
    },
]

FAKE_FS = {
    "src": ["src/app.py", "src/utils.py"],
    "src/app.py": "from utils import greet\nprint(greet('z'))\n",
    "src/utils.py": "def greet(name):\n    return f'hello {name}'\n",
}


def run_tool(name: str, args: dict) -> str:
    if name == "list_files":
        return json.dumps(FAKE_FS.get(args.get("directory", ""), []))
    if name == "read_file":
        return FAKE_FS.get(args.get("path", ""), "ERROR: file not found")
    return "ERROR: unknown tool"


def test_single_call(client: OpenAI, model: str) -> bool:
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Read the file src/app.py."}],
        tools=TOOLS,
        tool_choice="auto",
    )
    calls = resp.choices[0].message.tool_calls or []
    ok = len(calls) >= 1 and calls[0].function.name == "read_file"
    if ok:
        args = json.loads(calls[0].function.arguments)
        ok = args.get("path") == "src/app.py"
    print(f"[{'OK' if ok else 'FAIL'}] single tool call")
    return ok


def test_multi_turn(client: OpenAI, model: str, max_turns: int = 8) -> bool:
    messages: list[dict] = [
        {
            "role": "user",
            "content": "List the files in the src directory, read all of them, then explain in one sentence what the program does.",
        }
    ]
    read: set[str] = set()
    for _ in range(max_turns):
        resp = client.chat.completions.create(model=model, messages=messages, tools=TOOLS, tool_choice="auto")
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            ok = {"src/app.py", "src/utils.py"} <= read and bool(msg.content)
            print(f"[{'OK' if ok else 'FAIL'}] multi-turn tool calls (files read: {sorted(read)})")
            return ok
        for call in msg.tool_calls:
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError:
                print(f"[FAIL] invalid JSON arguments: {call.function.arguments!r}")
                return False
            if call.function.name == "read_file":
                read.add(args.get("path", ""))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": run_tool(call.function.name, args)})
    print("[FAIL] multi-turn tool calls: no final answer within the turn limit")
    return False


def test_structured_output(client: OpenAI, model: str) -> bool:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": (
                    "Answer only with a JSON object with the keys "
                    '"severity" (one of low, medium, high, critical) and "reason" (string). '
                    "Finding: log4j 2.14 library on an Internet-facing service."
                ),
            }
        ],
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or ""
    try:
        data = json.loads(content)
        ok = data.get("severity") in {"low", "medium", "high", "critical"} and isinstance(data.get("reason"), str)
    except json.JSONDecodeError:
        ok = False
    print(f"[{'OK' if ok else 'FAIL'}] structured JSON output")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)
    print(f"Model under test: {args.model}")
    results = []
    for test in (test_single_call, test_multi_turn, test_structured_output):
        try:
            results.append(test(client, args.model))
        except Exception as exc:  # noqa: BLE001 — we want the diagnosis, not a traceback
            print(f"[FAIL] {test.__name__}: {exc}")
            results.append(False)
    passed = all(results)
    print("RESULT:", "usable for Z" if passed else "do not use for agentic tasks")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
