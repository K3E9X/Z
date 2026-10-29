"""Terminal palettes from design/tokens.css (night, day, slate, contrast), as Rich styles."""

from __future__ import annotations

from dataclasses import dataclass

from rich.style import Style
from rich.theme import Theme

ACCENT = "#F25C2A"


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    raised: str
    text: str
    text_2: str
    text_3: str
    run: str
    wait: str
    ok: str
    err: str
    accent: str = ACCENT


PALETTES: dict[str, Palette] = {
    "night": Palette(
        "night", "#0C0C0B", "#1A1A18", "#EDECE8", "#A9A79F", "#8B8981", "#6AA6FF", "#E7B04A", "#5DBE8A", "#F07067"
    ),
    "day": Palette(
        "day", "#F7F7F5", "#F1F1EE", "#171715", "#5A5953", "#6F6D66", "#1F5FD1", "#9A5B00", "#1C7A4A", "#B92B27"
    ),
    "slate": Palette(
        "slate", "#0D1015", "#1A1F28", "#E4E9F1", "#A2ACBB", "#8792A3", "#74AEFF", "#E7B04A", "#5DBE8A", "#F07067"
    ),
    "contrast": Palette(
        "contrast", "#000000", "#161616", "#FFFFFF", "#E2E2E2", "#C9C9C9", "#8CC2FF", "#FFD166", "#7EE2A8", "#FF8F87"
    ),
}
DEFAULT = "night"
THEME_NAMES: tuple[str, ...] = tuple(PALETTES)


def palette(name: str | None) -> Palette:
    return PALETTES.get(name or DEFAULT, PALETTES[DEFAULT])


def rich_theme(name: str | None) -> Theme:
    p = palette(name)
    return Theme(
        {
            "z.text": Style(color=p.text),
            "z.m": Style(color=p.text_2),
            "z.d": Style(color=p.text_3),
            "z.run": Style(color=p.run),
            "z.wait": Style(color=p.wait),
            "z.ok": Style(color=p.ok),
            "z.err": Style(color=p.err),
            "z.acc": Style(color=p.accent),
            "z.b": Style(bold=True),
            "z.key": Style(color=p.text, bgcolor=p.raised),
            "z.sel": Style(bgcolor=p.raised),
        }
    )


# semantic state -> style name
STATE_STYLE = {
    "running": "z.run",
    "queued": "z.d",
    "draft": "z.d",
    "planned": "z.wait",
    "waiting": "z.wait",
    "approval": "z.wait",
    "question": "z.wait",
    "needs_attention": "z.wait",
    "paused": "z.wait",
    "pending": "z.wait",
    "succeeded": "z.ok",
    "applied": "z.ok",
    "approved": "z.ok",
    "healthy": "z.ok",
    "available": "z.d",
    "idle": "z.d",
    "working": "z.run",
    "failed": "z.err",
    "timed_out": "z.err",
    "budget_exceeded": "z.err",
    "rejected": "z.err",
    "unreachable": "z.err",
    "disabled": "z.d",
    "cancelled": "z.d",
    "superseded": "z.d",
    "skipped": "z.d",
    "degraded": "z.wait",
    "token_expiring": "z.wait",
    "applying": "z.run",
}


def state_style(state: str | None) -> str:
    return STATE_STYLE.get(state or "", "z.text")
