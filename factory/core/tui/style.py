"""Shared TUI styles (port of codex-rs/tui/src/style.rs).

Style choices are derived from the terminal's default background color
so that the user message, accent, and table separator styles blend
in nicely across light and dark themes.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .color import blend, is_light
from .terminal_palette import (
    StdoutColorLevel,
    best_color,
    default_bg,
    default_fg,
    rgb_color,
    stdout_color_level,
)


RGB = Tuple[int, int, int]


# -- Named accent constants (port one-to-one) --------------------------------------

LIGHT_BG_ACCENT_RGB: RGB = (0, 95, 135)
TABLE_SEPARATOR_FG_ALPHA: float = 0.20


# -- Style dataclass (Python analogue of ratatui::style::Style) ---------------

class Style:
    """Minimal Python analogue of ``ratatui::style::Style``."""

    def __init__(
        self,
        fg: Optional[object] = None,
        bg: Optional[object] = None,
        bold: bool = False,
        dim: bool = False,
        italic: bool = False,
        underline: bool = False,
    ) -> None:
        self.fg = fg
        self.bg = bg
        self.bold = bold
        self.dim = dim
        self.italic = italic
        self.underline = underline

    def with_fg(self, fg: object) -> "Style":
        return Style(fg=fg, bg=self.bg, bold=self.bold, dim=self.dim, italic=self.italic, underline=self.underline)

    def with_bg(self, bg: object) -> "Style":
        return Style(fg=self.fg, bg=bg, bold=self.bold, dim=self.dim, italic=self.italic, underline=self.underline)

    def with_bold(self) -> "Style":
        return Style(fg=self.fg, bg=self.bg, bold=True, dim=self.dim, italic=self.italic, underline=self.underline)

    def with_dim(self) -> "Style":
        return Style(fg=self.fg, bg=self.bg, bold=self.bold, dim=True, italic=self.italic, underline=self.underline)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Style):
            return NotImplemented
        return (
            self.fg == other.fg
            and self.bg == other.bg
            and self.bold == other.bold
            and self.dim == other.dim
            and self.italic == other.italic
            and self.underline == other.underline
        )

    def __hash__(self) -> int:
        return hash((self.fg, self.bg, self.bold, self.dim, self.italic, self.underline))


# -- Public API (port of user_message_style / proposed_plan_style / ...) ------

def user_message_style() -> Style:
    """Style for a user-authored message (port of ``user_message_style``)."""
    return user_message_style_for(default_bg())


def proposed_plan_style() -> Style:
    """Style for the proposed plan block (port of ``proposed_plan_style``)."""
    return proposed_plan_style_for(default_bg())


def table_separator_style() -> Style:
    """Low-contrast rule style for markdown table separators.

    Port of ``codex::style::table_separator_style``.
    """
    return table_separator_style_for(default_fg(), default_bg(), stdout_color_level())


def accent_style() -> Style:
    """Shared accent style for active or selected TUI controls.

    Port of ``codex::style::accent_style``.
    """
    return accent_style_for(default_bg())


# -- Per-bg variants (port one-to-one) ----------------------------------------------

def user_message_style_for(terminal_bg: Optional[RGB]) -> Style:
    """User message style for the given terminal background (port one-to-one)."""
    if terminal_bg is None:
        return Style()
    return Style(bg=user_message_bg(terminal_bg))


def proposed_plan_style_for(terminal_bg: Optional[RGB]) -> Style:
    """Proposed plan style for the given terminal background (port one-to-one)."""
    if terminal_bg is None:
        return Style()
    return Style(bg=proposed_plan_bg(terminal_bg))


def accent_style_for(terminal_bg: Optional[RGB]) -> Style:
    """Accent style for the given terminal background (port one-to-one)."""
    if terminal_bg is not None and is_light(terminal_bg):
        return Style(fg=best_color(LIGHT_BG_ACCENT_RGB), bold=True)
    return Style(fg=("indexed", 6), bold=True)  # 6 ≈ Cyan in 16-color ANSI


# -- Internal helpers (port one-to-one) --------------------------------------------

def table_separator_style_for(
    terminal_fg: Optional[RGB],
    terminal_bg: Optional[RGB],
    color_level: str,
) -> Style:
    """Compute the table separator style (port one-to-one)."""
    if terminal_fg is None or terminal_bg is None:
        return Style(dim=True)
    separator_rgb = blend(terminal_fg, terminal_bg, TABLE_SEPARATOR_FG_ALPHA)
    if color_level == StdoutColorLevel.TrueColor:
        return Style(fg=rgb_color(separator_rgb))
    if color_level == StdoutColorLevel.Ansi256:
        return Style(fg=best_color(separator_rgb))
    return Style(dim=True)


def user_message_bg(terminal_bg: RGB) -> object:
    """Pick a subtly-tinted background color for the user message (port one-to-one)."""
    if is_light(terminal_bg):
        top: RGB = (0, 0, 0)
        alpha = 0.04
    else:
        top = (255, 255, 255)
        alpha = 0.12
    return best_color(blend(top, terminal_bg, alpha))


def proposed_plan_bg(terminal_bg: RGB) -> object:
    """Background for the proposed plan block (port one-to-one)."""
    return user_message_bg(terminal_bg)


__all__ = [
    "LIGHT_BG_ACCENT_RGB",
    "Style",
    "TABLE_SEPARATOR_FG_ALPHA",
    "accent_style",
    "accent_style_for",
    "proposed_plan_bg",
    "proposed_plan_style",
    "proposed_plan_style_for",
    "table_separator_style",
    "table_separator_style_for",
    "user_message_bg",
    "user_message_style",
    "user_message_style_for",
]
