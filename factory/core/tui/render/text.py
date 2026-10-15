"""Core ratatui-style types used by the TUI port (Line, Span, Style, Color, Modifier).

The Python port mirrors the ``ratatui::text``, ``ratatui::style`` and
``ratatui::layout`` shapes so the rest of the codex port can refer to
them by their original names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntFlag
from typing import Any, Iterable, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Color
# ---------------------------------------------------------------------------

class Color:
    """Port of ``ratatui::style::Color``.

    The Rust enum is large (Reset + named colors + RGB + Indexed); in the
    Python port we expose a small wrapper that holds a ``tag`` plus
    optional ``r, g, b`` / ``index`` and a couple of class-level sentinels.
    """

    RESET = "reset"

    def __init__(self, tag: str = "reset", r: int = 0, g: int = 0, b: int = 0, index: int = 0) -> None:
        self.tag = tag
        self.r = r
        self.g = g
        self.b = b
        self.index = index

    @classmethod
    def reset(cls) -> "Color":
        return cls(cls.RESET)

    @classmethod
    def black(cls) -> "Color":
        return cls("black")

    @classmethod
    def red(cls) -> "Color":
        return cls("red")

    @classmethod
    def green(cls) -> "Color":
        return cls("green")

    @classmethod
    def yellow(cls) -> "Color":
        return cls("yellow")

    @classmethod
    def blue(cls) -> "Color":
        return cls("blue")

    @classmethod
    def magenta(cls) -> "Color":
        return cls("magenta")

    @classmethod
    def cyan(cls) -> "Color":
        return cls("cyan")

    @classmethod
    def gray(cls) -> "Color":
        return cls("gray")

    @classmethod
    def dark_gray(cls) -> "Color":
        return cls("dark_gray")

    @classmethod
    def light_red(cls) -> "Color":
        return cls("light_red")

    @classmethod
    def light_green(cls) -> "Color":
        return cls("light_green")

    @classmethod
    def light_yellow(cls) -> "Color":
        return cls("light_yellow")

    @classmethod
    def light_blue(cls) -> "Color":
        return cls("light_blue")

    @classmethod
    def light_magenta(cls) -> "Color":
        return cls("light_magenta")

    @classmethod
    def light_cyan(cls) -> "Color":
        return cls("light_cyan")

    @classmethod
    def white(cls) -> "Color":
        return cls("white")

    @classmethod
    def from_rgb(cls, r: int, g: int, b: int) -> "Color":
        return cls("rgb", r=r, g=g, b=b)

    @classmethod
    def from_indexed(cls, index: int) -> "Color":
        return cls("indexed", index=index)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Color):
            return NotImplemented
        return (
            self.tag == other.tag
            and self.r == other.r
            and self.g == other.g
            and self.b == other.b
            and self.index == other.index
        )

    def __hash__(self) -> int:
        return hash((self.tag, self.r, self.g, self.b, self.index))

    def __repr__(self) -> str:
        if self.tag == "rgb":
            return f"Color.rgb({self.r}, {self.g}, {self.b})"
        if self.tag == "indexed":
            return f"Color.indexed({self.index})"
        return f"Color.{self.tag}"


# ---------------------------------------------------------------------------
# Modifier
# ---------------------------------------------------------------------------

class Modifier(IntFlag):
    """Port of ``ratatui::style::Modifier``."""
    EMPTY = 0
    BOLD = 1 << 0
    DIM = 1 << 1
    ITALIC = 1 << 2
    UNDERLINED = 1 << 3
    SLOW_BLINK = 1 << 4
    RAPID_BLINK = 1 << 5
    REVERSED = 1 << 6
    HIDDEN = 1 << 7
    CROSSED_OUT = 1 << 8


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------

@dataclass
class Style:
    """Port of ``ratatui::style::Style``."""
    fg: Optional[Color] = None
    bg: Optional[Color] = None
    add_modifier: Modifier = Modifier.EMPTY
    sub_modifier: Modifier = Modifier.EMPTY

    def patch(self, other: "Style") -> "Style":
        """Return a copy of ``self`` with non-empty fields from ``other`` applied."""
        fg = other.fg if other.fg is not None else self.fg
        bg = other.bg if other.bg is not None else self.bg
        add_modifier = self.add_modifier | (other.add_modifier & ~other.sub_modifier)
        sub_modifier = self.sub_modifier | (other.sub_modifier & ~other.add_modifier)
        return Style(
            fg=fg,
            bg=bg,
            add_modifier=add_modifier,
            sub_modifier=sub_modifier,
        )

    def bold(self) -> "Style":
        return Style(
            fg=self.fg, bg=self.bg,
            add_modifier=self.add_modifier | Modifier.BOLD,
            sub_modifier=self.sub_modifier,
        )


# ---------------------------------------------------------------------------
# Span
# ---------------------------------------------------------------------------

@dataclass
class Span:
    """Port of ``ratatui::text::Span``."""
    content: str = ""
    style: Style = field(default_factory=Style)

    @classmethod
    def raw(cls, content: str) -> "Span":
        return cls(content=content, style=Style())

    @classmethod
    def styled(cls, content: str, style: Style) -> "Span":
        return cls(content=content, style=style)

    def width(self) -> int:
        return _display_width(self.content)


def span_styled(content: str, style: Style) -> Span:
    return Span(content=content, style=style)


# ---------------------------------------------------------------------------
# Line
# ---------------------------------------------------------------------------

@dataclass
class Line:
    """Port of ``ratatui::text::Line``."""
    spans: List[Span] = field(default_factory=list)
    style: Style = field(default_factory=Style)

    def __init__(self, spans: Union[Span, str, List[Span], "Line", None] = None, style: Optional[Style] = None) -> None:
        if isinstance(spans, Line):
            self.spans = list(spans.spans)
            self.style = spans.style
        elif isinstance(spans, str):
            self.spans = [Span(content=spans, style=Style())]
            self.style = style or Style()
        elif isinstance(spans, Span):
            self.spans = [spans]
            self.style = style or Style()
        elif isinstance(spans, list):
            self.spans = list(spans)
            self.style = style or Style()
        else:
            self.spans = []
            self.style = style or Style()

    def from_spans(spans: Iterable[Span], style: Optional[Style] = None) -> "Line":
        return Line(list(spans), style=style)

    def width(self) -> int:
        return sum(s.width() for s in self.spans)

    def patch_style(self, style: Style) -> "Line":
        new_style = self.style.patch(style)
        return Line(
            [Span(s.content, s.style.patch(style)) for s in self.spans],
            style=new_style,
        )


def from_string(text: str, style: Optional[Style] = None) -> Line:
    return Line(text, style=style)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _display_width(s: str) -> int:
    """Approximate ``UnicodeWidth::width`` for ASCII (port one-to-one within scope)."""
    width = 0
    for ch in s:
        cp = ord(ch)
        if cp < 0x20:
            width += 0
        elif cp < 0x7F:
            width += 1
        elif cp < 0xA0:
            width += 1
        else:
            width += 1
    return width


__all__ = [
    "Color",
    "Line",
    "Modifier",
    "Span",
    "Style",
    "from_string",
    "span_styled",
]
