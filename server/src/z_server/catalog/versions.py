"""Semantic version helpers."""

from __future__ import annotations


def parse(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(p) for p in version.split("."))
    return major, minor, patch


def bump_minor(version: str) -> str:
    major, minor, _ = parse(version)
    return f"{major}.{minor + 1}.0"


def newer(a: str, b: str) -> bool:
    return parse(a) > parse(b)
