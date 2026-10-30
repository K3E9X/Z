"""Atlassian Document Format (Jira Cloud REST v3, Confluence) <-> plain text with light Markdown.

Writes: agents and reviewers work in text; the executor converts to ADF (paragraphs, headings `#`, bullet and
ordered lists, fenced code blocks, inline **bold**, *italic*, `code` and [links](https://...)).
Reads: ADF in tool results is flattened to text before it reaches the agent (fewer tokens, nothing hidden in
attributes). Only http(s) and mailto links are kept.
"""

from __future__ import annotations

import re
from typing import Any

INLINE = re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\)|(?<![*\w])\*[^*\n]+\*(?![*\w]))")
LINK = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)$")
SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)
BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _text(value: str, marks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": value}
    if marks:
        node["marks"] = marks
    return node


def inline(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append(_text(part[2:-2], [{"type": "strong"}]))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append(_text(part[1:-1], [{"type": "code"}]))
        elif (m := LINK.match(part)) and SAFE_HREF.match(m.group(2)):
            out.append(_text(m.group(1), [{"type": "link", "attrs": {"href": m.group(2)}}]))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append(_text(part[1:-1], [{"type": "em"}]))
        else:
            out.append(_text(part))
    return out


def _paragraph(lines: list[str]) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        if i:
            content.append({"type": "hardBreak"})
        content.extend(inline(line))
    return {"type": "paragraph", "content": content}


def _list(kind: str, items: list[str]) -> dict[str, Any]:
    return {
        "type": kind,
        "content": [{"type": "listItem", "content": [{"type": "paragraph", "content": inline(i)}]} for i in items],
    }


def to_adf(text: str) -> dict[str, Any]:
    """Text with light Markdown -> ADF document (always valid, possibly an empty paragraph)."""
    blocks: list[dict[str, Any]] = []
    lines = (text or "").replace("\r\n", "\n").split("\n")
    i = 0
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(_paragraph(paragraph.copy()))
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush()
            language = line.strip()[3:].strip()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            node: dict[str, Any] = {"type": "codeBlock", "content": [_text("\n".join(code))] if code else []}
            if language:
                node["attrs"] = {"language": language}
            blocks.append(node)
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        if m := HEADING.match(line):
            flush()
            blocks.append({"type": "heading", "attrs": {"level": len(m.group(1))}, "content": inline(m.group(2))})
            i += 1
            continue
        for pattern, kind in ((BULLET, "bulletList"), (ORDERED, "orderedList")):
            if pattern.match(line):
                flush()
                items = []
                while i < len(lines) and (m2 := pattern.match(lines[i])):
                    items.append(m2.group(1))
                    i += 1
                blocks.append(_list(kind, items))
                break
        else:
            paragraph.append(line)
            i += 1
    flush()
    return {"type": "doc", "version": 1, "content": blocks or [{"type": "paragraph", "content": []}]}


def to_text(node: Any) -> str:
    """ADF node or document -> text. Non-ADF values are returned as strings."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return str(node)
    return _render(node).strip()


def _render(node: dict[str, Any], depth: int = 0) -> str:
    """`depth` is the list nesting level (indentation of list markers)."""
    kind = node.get("type")
    children = node.get("content") or []
    if kind == "text":
        text = str(node.get("text", ""))
        for mark in node.get("marks") or []:
            if mark.get("type") == "link":
                href = (mark.get("attrs") or {}).get("href", "")
                if SAFE_HREF.match(str(href)):
                    text = f"[{text}]({href})"
            elif mark.get("type") == "code":
                text = f"`{text}`"
        return text
    if kind == "hardBreak":
        return "\n"
    if kind in {"mention", "emoji", "date", "status", "inlineCard"}:
        attrs = node.get("attrs") or {}
        return str(attrs.get("text") or attrs.get("shortName") or attrs.get("url") or attrs.get("timestamp") or "")
    inner = "".join(_render(c, depth) for c in children if isinstance(c, dict))
    if kind == "paragraph":
        return inner + "\n\n"
    if kind == "heading":
        return "#" * int((node.get("attrs") or {}).get("level", 1)) + " " + inner + "\n\n"
    if kind == "codeBlock":
        return "```\n" + inner + "\n```\n\n"
    if kind in {"bulletList", "orderedList"}:
        lines = []
        for n, item in enumerate(c for c in children if isinstance(c, dict)):
            marker = f"{n + 1}." if kind == "orderedList" else "-"
            body = re.sub(r"\n{2,}", "\n", _render(item, depth + 1).strip())
            lines.append("  " * depth + f"{marker} {body}")
        return "\n".join(lines) + "\n\n"
    if kind == "blockquote":
        return "\n".join("> " + line for line in inner.strip().splitlines()) + "\n\n"
    if kind == "rule":
        return "---\n\n"
    if kind == "table":
        rows = [" | ".join(_render(cell).strip() for cell in row.get("content") or []) for row in children]
        return "\n".join(rows) + "\n\n"
    return inner


def flatten(value: Any) -> Any:
    """Replace every ADF document inside a JSON value by its text (descriptions, comment bodies, fields)."""
    if isinstance(value, dict):
        if value.get("type") == "doc" and "content" in value:
            return to_text(value)
        return {k: flatten(v) for k, v in value.items()}
    if isinstance(value, list):
        return [flatten(v) for v in value]
    return value
