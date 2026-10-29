"""`read` and `edit`: workspace file tools, jailed to the workspace (docs/04 §1 neutral tool names)."""

from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Any

from z_common.models.common import FieldError

from z_runner.tools.base import ArgumentError, ToolContext, ToolResult, ZTool, clip, untrusted
from z_runner.workspace import PRIVATE_DIR, JailError

MAX_LIST = 500
MAX_READ_BYTES = 5 * 1024 * 1024


def _is_private(ctx: ToolContext, path: Path) -> bool:
    rel = path.relative_to(ctx.workspace.root)
    return rel.parts[:1] == (PRIVATE_DIR,)


class ReadTool(ZTool):
    name = "read"
    kind = "read"
    read_only = True
    description = (
        "Read a text file or list a directory in the workspace. Paths are relative to the workspace root. "
        "Returns numbered lines. Use offset/limit for long files."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "minLength": 1, "description": "File or directory, relative to the workspace."},
            "offset": {"type": "integer", "minimum": 1, "description": "First line to return (1-based)."},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2000,
                "description": "Number of lines (default 400).",
            },
        },
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw = str(args["path"])
        try:
            path = ctx.workspace.resolve(raw)
        except JailError as exc:
            return ctx.jail_denial(raw, exc)
        rel = ctx.workspace.relative(path)
        if not path.exists():
            return ToolResult(text=f"No such file or directory: {rel}", ok=False)
        if path.is_dir():
            entries = sorted(os.listdir(path))
            shown = [
                e + ("/" if (path / e).is_dir() else "") for e in entries[:MAX_LIST] if e != PRIVATE_DIR or rel != "."
            ]
            more = f"\n… {len(entries) - MAX_LIST} more entries" if len(entries) > MAX_LIST else ""
            listing = "\n".join(shown) or "(empty directory)"
            return ToolResult(
                text=untrusted(f"workspace:{rel}/", ctx.redactor.text(listing) + more), summary=f"listed {rel}"
            )
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            return ToolResult(
                text=f"{rel} is {size} bytes, too large to read at once; use the terminal to inspect it.", ok=False
            )
        data = path.read_bytes()
        if b"\x00" in data[:8192]:
            return ToolResult(text=f"{rel} is a binary file ({size} bytes).", summary=f"{rel}: binary")
        lines = data.decode("utf-8", errors="replace").splitlines()
        offset = int(args.get("offset", 1))
        limit = int(args.get("limit", 400))
        chunk = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(chunk, start=offset))
        tail = ""
        if offset - 1 + limit < len(lines):
            tail = f"\n… {len(lines) - (offset - 1 + limit)} more lines (use offset={offset + limit})"
        body = clip(ctx.redactor.text(numbered)) + tail
        if _is_private(ctx, path):
            text = body  # skill resources shipped by Z, not workspace input
        else:
            text = untrusted(f"workspace:{rel}", body, flagged=rel in ctx.staged.flagged)
        return ToolResult(text=text, summary=f"read {rel} ({len(chunk)} of {len(lines)} lines)")


class EditTool(ZTool):
    name = "edit"
    kind = "edit"
    description = (
        "Create or change a file in the workspace. command=write replaces the whole file with `content` "
        "(parents are created). command=replace substitutes `old_text` with `new_text`; `old_text` must "
        "appear exactly once unless replace_all is true."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["command", "path"],
        "properties": {
            "command": {"type": "string", "enum": ["write", "replace"]},
            "path": {"type": "string", "minLength": 1},
            "content": {"type": "string", "description": "write: the full new file content."},
            "old_text": {"type": "string", "minLength": 1, "description": "replace: exact text to find."},
            "new_text": {"type": "string", "description": "replace: replacement text."},
            "replace_all": {"type": "boolean"},
        },
    }

    def __init__(self, only_paths: frozenset[str] | None = None) -> None:
        """`only_paths`: when the agent profile has no `edit` tool, the runner still lets it write its report and
        the step output file (and nothing else)."""
        self.only_paths = only_paths
        if only_paths:
            self.description = (
                "Write your report (and the step output file, when the step has one). Only these paths can be written: "
                + ", ".join(sorted(only_paths))
                + ". command=write replaces the whole file with `content`."
            )

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        missing = ["content"] if args["command"] == "write" else ["old_text", "new_text"]
        missing = [key for key in missing if key not in args]
        if missing:
            raise ArgumentError(
                [
                    FieldError(path=key, message=f"'{key}' is required when command is {args['command']}")
                    for key in missing
                ]
            )
        raw = str(args["path"])
        try:
            path = ctx.workspace.resolve(raw, for_write=True)
        except JailError as exc:
            return ctx.jail_denial(raw, exc)
        rel = ctx.workspace.relative(path)
        if _is_private(ctx, path):
            return ctx.jail_denial(raw, JailError(f"{PRIVATE_DIR}/ is reserved for the platform"))
        if self.only_paths is not None and rel not in self.only_paths:
            ctx.deny(raw, "not_in_allowlist", f"this agent may only write {', '.join(sorted(self.only_paths))}")
            return ToolResult(
                text=f"Refused: this agent may only write {', '.join(sorted(self.only_paths))}.", ok=False
            )
        if path.is_dir():
            return ToolResult(text=f"{rel} is a directory.", ok=False)
        before = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        if args["command"] == "write":
            after = str(args["content"])
        else:
            if not path.exists():
                return ToolResult(text=f"No such file: {rel}", ok=False)
            old, new = str(args["old_text"]), str(args["new_text"])
            count = before.count(old)
            if count == 0:
                return ToolResult(
                    text=f"old_text was not found in {rel}. Read the file again and copy the exact text.", ok=False
                )
            if count > 1 and not args.get("replace_all"):
                return ToolResult(
                    text=f"old_text appears {count} times in {rel}; add context to make it unique or set replace_all.",
                    ok=False,
                )
            after = before.replace(old, new) if args.get("replace_all") else before.replace(old, new, 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(after, encoding="utf-8")
        added = removed = 0
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        ctx.sink.emit("file_edit", {"path": rel, "added": added, "removed": removed})
        ctx.progress.append(f"edit:{rel}")
        return ToolResult(
            text=f"{rel}: {added} line(s) added, {removed} removed.", summary=f"edited {rel} (+{added} -{removed})"
        )
