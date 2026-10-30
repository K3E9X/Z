"""Shared Textual pieces: themes from design/tokens.css, decision prompts, confirmations, text prompts."""

from __future__ import annotations

from typing import Any, ClassVar

from rich.console import Group, RenderableType
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import Input, Static

from z_cli.glyphs import POINTER
from z_cli.theme import Palette, palette, rich_theme


def textual_theme(p: Palette) -> Theme:
    return Theme(
        name=f"z-{p.name}",
        primary=p.run,
        secondary=p.text_2,
        warning=p.wait,
        error=p.err,
        success=p.ok,
        accent=p.accent,
        foreground=p.text,
        background=p.bg,
        surface=p.bg,
        panel=p.raised,
        boost=p.raised,
        dark=p.name != "day",
    )


def apply_theme(app: App[Any], name: str | None) -> str:
    """Use a Z palette for Textual and for the Rich styles (z.run, z.wait...) the renderers emit."""
    p = palette(name)
    theme = textual_theme(p)
    app.register_theme(theme)
    app.theme = theme.name
    console = app.console
    if getattr(app, "_z_rich_theme", False):  # replace the Z theme pushed earlier, not Textual's own
        console.pop_theme()
    console.push_theme(rich_theme(p.name))
    app._z_rich_theme = True  # type: ignore[attr-defined]
    return p.name


def choices_text(options: list[str], selected: int) -> Text:
    text = Text()
    for i, option in enumerate(options):
        if i:
            text.append("\n")
        text.append(f"{POINTER} " if i == selected else "  ", style="z.acc")
        text.append(f"{i + 1}. {option}", style="z.b" if i == selected else "z.text")
    return text


class Choice(Widget, can_focus=True):
    """A boxed prompt with numbered choices: arrows or digits, Enter to pick, Esc to dismiss."""

    DEFAULT_CSS = """
    Choice { height: auto; border: round $warning; padding: 0 1; margin: 1 0 0 0; }
    Choice.danger { border: round $error; }
    Choice:focus { border: round $accent; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up,k", "move(-1)", "up", show=False),
        Binding("down,j", "move(1)", "down", show=False),
        Binding("enter", "pick", "pick", show=False),
        Binding("escape", "dismiss", "dismiss", show=False),
    ]

    class Chosen(Message):
        def __init__(self, choice: Choice, index: int | None) -> None:
            super().__init__()
            self.choice = choice
            self.index = index

    def __init__(
        self, body: RenderableType | None, options: list[str], *, key: str = "", danger: bool = False, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.body = body
        self.options = options
        self.selected = 0
        self.key = key
        if danger:
            self.add_class("danger")

    def render(self) -> RenderableType:
        parts: list[RenderableType] = []
        if self.body is not None:
            parts += [self.body, Text("")]
        parts.append(choices_text(self.options, self.selected))
        return Group(*parts)

    def action_move(self, delta: int) -> None:
        self.selected = (self.selected + delta) % len(self.options)
        self.refresh(layout=True)

    def action_pick(self) -> None:
        self.post_message(self.Chosen(self, self.selected))

    def action_dismiss(self) -> None:
        self.post_message(self.Chosen(self, None))

    def on_key(self, event: events.Key) -> None:
        if event.character and event.character.isdigit():
            n = int(event.character)
            if 1 <= n <= len(self.options):
                event.stop()
                self.selected = n - 1
                self.refresh(layout=True)
                self.post_message(self.Chosen(self, n - 1))


class ChoiceScreen(ModalScreen[int | None]):
    """Choice as a modal (z top)."""

    DEFAULT_CSS = """
    ChoiceScreen { align: center middle; }
    ChoiceScreen > Vertical { width: auto; max-width: 110; height: auto; max-height: 90%; background: $background; }
    """

    def __init__(self, body: RenderableType | None, options: list[str], *, danger: bool = False) -> None:
        super().__init__()
        self.body, self.options, self.danger = body, options, danger

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Choice(self.body, self.options, danger=self.danger)

    def on_mount(self) -> None:
        self.query_one(Choice).focus()

    def on_choice_chosen(self, message: Choice.Chosen) -> None:
        message.stop()
        self.dismiss(message.index)


class PromptScreen(ModalScreen[str | None]):
    """One line of text (a reason, a filter, a command, an operation id to confirm)."""

    DEFAULT_CSS = """
    PromptScreen { align: center middle; }
    PromptScreen > Vertical { width: 90; height: auto; border: round $warning; background: $background; padding: 0 1; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "cancel", show=False)]

    def __init__(self, title: str, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self.title_text, self.placeholder, self.value = title, placeholder, value

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.title_text, style="z.wait"))
            yield Input(value=self.value, placeholder=self.placeholder)

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextScreen(ModalScreen[None]):
    """A scrollable page of text (help, mission detail, logs); any of q, Esc closes it."""

    DEFAULT_CSS = """
    TextScreen { align: center middle; }
    TextScreen > Vertical { width: 95%; height: 90%; border: round $secondary; background: $background; padding: 0 1; overflow-y: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape,q", "close", "close", show=False)]

    def __init__(self, body: RenderableType) -> None:
        super().__init__()
        self.body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.body, id="text-body")

    def update(self, body: RenderableType) -> None:
        self.body = body
        self.query_one("#text-body", Static).update(body)

    def action_close(self) -> None:
        self.dismiss(None)
