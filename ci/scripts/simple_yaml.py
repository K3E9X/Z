"""Strict loader for the small YAML subset used by `security/*.yaml` (stdlib only, fails closed).

The CI guard scripts must run before any dependency is installed (a compromised package must not be able to
silence them), so they cannot use PyYAML. This loader accepts:

- block mappings and block sequences (spaces only, consistent indentation), a sequence item may start a
  mapping on the dash line (``- id: CVE-1``);
- flow mappings and sequences on one line (``{id: CVE-1, versions: ["1.0", "1.1"]}``);
- plain, single-quoted and double-quoted scalars, ``#`` comments, an optional leading ``---``.

Every scalar stays a string (``2026-12-31`` and ``1.10`` are not converted); ``null``, ``~`` and empty values
are ``None``. Anything else (anchors, aliases, tags, block scalars, multi-line flow collections, duplicate keys,
tabs) raises ``YamlSubsetError`` instead of being guessed.
"""

from __future__ import annotations

import re

Value = str | None | list["Value"] | dict[str, "Value"]

_KEY = re.compile(r"""^(?P<key>"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^\s'"{}\[\],#&*!|>%@`][^:#]*?)\s*:(?:\s+|$)""")
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "\r", "0": "\0"}


class YamlSubsetError(ValueError):
    """The document uses YAML outside the supported subset (or is malformed)."""


def load(text: str) -> Value:
    """Parse ``text``; an empty document is ``None``."""
    lines = _lines(text)
    if not lines:
        return None
    value, index = _block(lines, 0, lines[0][1])
    if index != len(lines):
        number, indent, _ = lines[index]
        raise YamlSubsetError(f"line {number}: unexpected indentation ({indent} spaces)")
    return value


def _lines(text: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlSubsetError(f"line {number}: tabs are not allowed for indentation")
        body = _strip_comment(raw, number).rstrip()
        if not body.strip():
            continue
        if body.strip() == "---" and not out:
            continue
        if body.strip() in {"---", "..."}:
            raise YamlSubsetError(f"line {number}: only one document is supported")
        indent = len(body) - len(body.lstrip(" "))
        out.append((number, indent, body.strip()))
    return out


def _strip_comment(raw: str, number: int) -> str:
    quote = ""
    i = 0
    while i < len(raw):
        char = raw[i]
        if quote == '"' and char == "\\":
            i += 2
            continue
        if quote:
            if char == quote:
                if quote == "'" and raw[i + 1 : i + 2] == "'":
                    i += 2
                    continue
                quote = ""
        elif char in "'\"" and (i == 0 or raw[i - 1] in " \t:-[{,"):
            quote = char
        elif char == "#" and (i == 0 or raw[i - 1] in " \t"):
            return raw[:i]
        i += 1
    if quote:
        raise YamlSubsetError(f"line {number}: unterminated quoted string")
    return raw


def _block(lines: list[tuple[int, int, str]], index: int, indent: int) -> tuple[Value, int]:
    if lines[index][2] == "-" or lines[index][2].startswith("- "):
        return _sequence(lines, index, indent)
    return _mapping(lines, index, indent)


def _sequence(lines: list[tuple[int, int, str]], index: int, indent: int) -> tuple[Value, int]:
    items: list[Value] = []
    while index < len(lines):
        number, line_indent, text = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise YamlSubsetError(f"line {number}: unexpected indentation ({line_indent} spaces)")
        if not (text == "-" or text.startswith("- ")):
            break
        content = text[1:].strip()
        if not content:
            if index + 1 < len(lines) and lines[index + 1][1] > indent:
                value, index = _block(lines, index + 1, lines[index + 1][1])
            else:
                value, index = None, index + 1
        elif _KEY.match(content) and content[0] not in "{[":
            # A mapping that starts on the dash line: its keys sit at the column of the first key.
            column = line_indent + (len(text) - len(content))
            patched = [*lines[:index], (number, column, content), *lines[index + 1 :]]
            value, index = _mapping(patched, index, column)
        else:
            value, index = _scalar_or_flow(content, number), index + 1
        items.append(value)
    return items, index


def _mapping(lines: list[tuple[int, int, str]], index: int, indent: int) -> tuple[Value, int]:
    result: dict[str, Value] = {}
    while index < len(lines):
        number, line_indent, text = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise YamlSubsetError(f"line {number}: unexpected indentation ({line_indent} spaces)")
        if text == "-" or text.startswith("- "):
            break
        match = _KEY.match(text)
        if not match:
            raise YamlSubsetError(f"line {number}: expected `key: value`, got {text!r}")
        key = _unquote(match.group("key").strip(), number)
        if key in result:
            raise YamlSubsetError(f"line {number}: duplicate key {key!r}")
        rest = text[match.end() :].strip()
        index += 1
        if rest:
            result[key] = _scalar_or_flow(rest, number)
        elif index < len(lines) and lines[index][1] > indent:
            result[key], index = _block(lines, index, lines[index][1])
        elif index < len(lines) and lines[index][1] == indent and lines[index][2].startswith("- "):
            result[key], index = _sequence(lines, index, indent)  # `key:` followed by `- item` at the same column
        else:
            result[key] = None
    return result, index


def _scalar_or_flow(text: str, number: int) -> Value:
    parser = _Flow(text, number)
    value = parser.value(top=True)
    parser.skip_spaces()
    if parser.pos != len(text):
        raise YamlSubsetError(f"line {number}: unexpected {text[parser.pos :]!r}")
    return value


def _unquote(token: str, number: int) -> str:
    if token.startswith('"'):
        out, i = [], 1
        while i < len(token) - 1:
            char = token[i]
            if char == "\\":
                escaped = token[i + 1 : i + 2]
                if escaped not in _ESCAPES:
                    raise YamlSubsetError(f"line {number}: unsupported escape \\{escaped}")
                out.append(_ESCAPES[escaped])
                i += 2
                continue
            out.append(char)
            i += 1
        return "".join(out)
    if token.startswith("'"):
        return token[1:-1].replace("''", "'")
    return token


class _Flow:
    """Recursive-descent parser for one line of flow YAML (or a single plain/quoted scalar)."""

    def __init__(self, text: str, number: int) -> None:
        self.text, self.number, self.pos = text, number, 0

    def fail(self, message: str) -> YamlSubsetError:
        return YamlSubsetError(f"line {self.number}: {message} in {self.text!r}")

    def skip_spaces(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] == " ":
            self.pos += 1

    def value(self, top: bool = False) -> Value:
        self.skip_spaces()
        if self.pos >= len(self.text):
            if top:
                return None
            raise self.fail("missing value")
        char = self.text[self.pos]
        if char == "{":
            return self.mapping()
        if char == "[":
            return self.sequence()
        if char in "&*!|>%@`":
            raise self.fail(f"unsupported YAML feature {char!r}")
        return self.scalar(top)

    def scalar(self, top: bool) -> Value:
        char = self.text[self.pos]
        if char in "'\"":
            end = self._quoted_end(char)
            token = self.text[self.pos : end + 1]
            self.pos = end + 1
            return _unquote(token, self.number)
        stop = "" if top else ",]}"
        start = self.pos
        while self.pos < len(self.text):
            here = self.text[self.pos]
            if here in stop:
                break
            if not top and here == ":" and self.text[self.pos + 1 : self.pos + 2] in {" ", ",", "}", ""}:
                break
            self.pos += 1
        token = self.text[start : self.pos].strip()
        if top and (": " in token or token.endswith(":")):
            raise self.fail("plain scalars cannot contain ': ' (quote the value)")
        if token in {"", "~", "null", "Null", "NULL"}:
            return None
        return token

    def _quoted_end(self, quote: str) -> int:
        i = self.pos + 1
        while i < len(self.text):
            if quote == '"' and self.text[i] == "\\":
                i += 2
                continue
            if self.text[i] == quote:
                if quote == "'" and self.text[i + 1 : i + 2] == "'":
                    i += 2
                    continue
                return i
            i += 1
        raise self.fail("unterminated quoted string")

    def expect(self, char: str) -> None:
        self.skip_spaces()
        if self.text[self.pos : self.pos + 1] != char:
            raise self.fail(f"expected {char!r}")
        self.pos += 1

    def sequence(self) -> Value:
        self.expect("[")
        items: list[Value] = []
        self.skip_spaces()
        if self.text[self.pos : self.pos + 1] == "]":
            self.pos += 1
            return items
        while True:
            items.append(self.value())
            self.skip_spaces()
            if self.text[self.pos : self.pos + 1] == ",":
                self.pos += 1
                continue
            self.expect("]")
            return items

    def mapping(self) -> Value:
        self.expect("{")
        result: dict[str, Value] = {}
        self.skip_spaces()
        if self.text[self.pos : self.pos + 1] == "}":
            self.pos += 1
            return result
        while True:
            key = self.value()
            if not isinstance(key, str):
                raise self.fail("mapping keys must be scalars")
            if key in result:
                raise self.fail(f"duplicate key {key!r}")
            self.expect(":")
            result[key] = self.value()
            self.skip_spaces()
            if self.text[self.pos : self.pos + 1] == ",":
                self.pos += 1
                continue
            self.expect("}")
            return result
