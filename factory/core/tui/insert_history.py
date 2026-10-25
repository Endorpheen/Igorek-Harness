"""Inserts finalized history rows into terminal scrollback (port of insert_history.rs).

Codex uses the terminal scrollback itself for finalized chat history, so
inserting a history cell is an escape-sequence operation rather than a
ratatui render.

The Python port preserves the original ``insert_history_lines(terminal,
lines: List[Line])`` signature with a ratatui-style ``Line`` (carries
``spans`` and ``style``). Internally the per-span information is
flattened into the plain-string representation expected by the rest of
the Python TUI pipeline (``HyperlinkLine.line: str``) so the existing
``terminal_hyperlinks`` / ``wrapping`` modules keep working unchanged.

The test module from the source is **not** ported per the project's
"no tests" policy.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, List, Optional, Protocol

from .render.text import Color as TuiColor
from .render.text import Line, Modifier, Span, Style
from .render.line_utils import line_to_static
from .terminal_hyperlinks import (
    HyperlinkLine,
    decorate_spans,
    plain_hyperlink_lines,
    remap_wrapped_line,
)
from .wrapping import (
    RtOptions,
    adaptive_wrap_line,
    line_contains_url_like,
    line_has_mixed_url_and_non_url_tokens,
)


# ---------------------------------------------------------------------------
# Public enums
# ---------------------------------------------------------------------------

class HistoryLineWrapPolicy(Enum):
    PreWrap = "pre_wrap"
    Terminal = "terminal"


class InsertHistoryMode(Enum):
    Standard = "standard"
    ZellijRaw = "zellij_raw"


# ---------------------------------------------------------------------------
# ANSI escape helpers (port of the various crossterm commands used here)
# ---------------------------------------------------------------------------

def _set_scroll_region(writer: io.IOBase, region_start: int, region_end: int) -> None:
    writer.write(f"\x1b[{region_start};{region_end}r".encode("utf-8"))


def _reset_scroll_region(writer: io.IOBase) -> None:
    writer.write(b"\x1b[r")


def _move_to(writer: io.IOBase, x: int, y: int) -> None:
    writer.write(f"\x1b[{y + 1};{x + 1}H".encode("utf-8"))


def _save_position(writer: io.IOBase) -> None:
    writer.write(b"\x1b7")


def _restore_position(writer: io.IOBase) -> None:
    writer.write(b"\x1b8")


def _move_down(writer: io.IOBase, n: int) -> None:
    if n > 0:
        writer.write(f"\x1b[{n}B".encode("utf-8"))


def _move_to_column(writer: io.IOBase, x: int) -> None:
    writer.write(f"\x1b[{x + 1}G".encode("utf-8"))


def _clear_until_newline(writer: io.IOBase) -> None:
    writer.write(b"\x1b[K")


def _print(writer: io.IOBase, text: str) -> None:
    writer.write(text.encode("utf-8", errors="replace"))


def _reverse_index(writer: io.IOBase, n: int) -> None:
    for _ in range(n):
        writer.write(b"\x1bM")


def _set_fg_bg(writer: io.IOBase, fg: TuiColor, bg: TuiColor) -> None:
    writer.write(_color_to_ansi(fg, is_bg=False).encode("utf-8"))
    writer.write(_color_to_ansi(bg, is_bg=True).encode("utf-8"))


def _reset_colors(writer: io.IOBase) -> None:
    writer.write(b"\x1b[0m")


def _color_to_ansi(color: TuiColor, is_bg: bool) -> str:
    """Return the ANSI SGR prefix for a given ``Color`` (port of crossterm's mapping)."""
    base = 40 if is_bg else 30
    if color is None or color.tag == "reset":
        return f"\x1b[{base + 9}m"
    if color.tag == "rgb":
        prefix = base + 8
        return f"\x1b[{prefix};2;{color.r};{color.g};{color.b}m"
    if color.tag == "indexed":
        prefix = base + 8
        return f"\x1b[{prefix};5;{color.index}m"
    named = {
        "black": 0, "red": 1, "green": 2, "yellow": 3,
        "blue": 4, "magenta": 5, "cyan": 6, "gray": 7,
        "dark_gray": 8, "light_red": 9, "light_green": 10, "light_yellow": 11,
        "light_blue": 12, "light_magenta": 13, "light_cyan": 14, "white": 15,
    }
    index = named.get(color.tag)
    code = base + 9 if index is None else (base + index if index < 8 else base + 60 + index - 8)
    return f"\x1b[{code}m"


# ---------------------------------------------------------------------------
# ModifierDiff (port of `ModifierDiff`)
# ---------------------------------------------------------------------------

@dataclass
class ModifierDiff:
    """Port of ``codex::insert_history::ModifierDiff``."""
    from_mod: Modifier
    to_mod: Modifier

    def queue(self, writer: io.IOBase) -> None:
        removed = Modifier(int(self.from_mod) & ~int(self.to_mod))
        if Modifier.REVERSED in removed:
            writer.write(b"\x1b[27m")
        if Modifier.BOLD in removed:
            writer.write(b"\x1b[22m")
            if Modifier.DIM in self.to_mod:
                writer.write(b"\x1b[2m")
        if Modifier.ITALIC in removed:
            writer.write(b"\x1b[23m")
        if Modifier.UNDERLINED in removed:
            writer.write(b"\x1b[24m")
        if Modifier.DIM in removed:
            writer.write(b"\x1b[22m")
        if Modifier.CROSSED_OUT in removed:
            writer.write(b"\x1b[29m")
        if (Modifier.SLOW_BLINK in removed) or (Modifier.RAPID_BLINK in removed):
            writer.write(b"\x1b[25m")

        added = Modifier(int(self.to_mod) & ~int(self.from_mod))
        if Modifier.REVERSED in added:
            writer.write(b"\x1b[7m")
        if Modifier.BOLD in added:
            writer.write(b"\x1b[1m")
        if Modifier.ITALIC in added:
            writer.write(b"\x1b[3m")
        if Modifier.UNDERLINED in added:
            writer.write(b"\x1b[4m")
        if Modifier.DIM in added:
            writer.write(b"\x1b[2m")
        if Modifier.CROSSED_OUT in added:
            writer.write(b"\x1b[9m")
        if Modifier.SLOW_BLINK in added:
            writer.write(b"\x1b[5m")
        if Modifier.RAPID_BLINK in added:
            writer.write(b"\x1b[6m")


# ---------------------------------------------------------------------------
# write_spans / write_history_line
# ---------------------------------------------------------------------------

def write_spans(writer: io.IOBase, spans: Iterable[Span]) -> None:
    """Emit ``spans`` to ``writer`` with minimal ANSI diff (port of ``write_spans``)."""
    fg: TuiColor = TuiColor.reset()
    bg: TuiColor = TuiColor.reset()
    last_modifier = Modifier.EMPTY
    for span in spans:
        modifier = span.style.add_modifier
        modifier = Modifier(int(modifier) & ~int(span.style.sub_modifier))
        if modifier != last_modifier:
            ModifierDiff(from_mod=last_modifier, to_mod=modifier).queue(writer)
            last_modifier = modifier
        next_fg = span.style.fg if span.style.fg is not None else TuiColor.reset()
        next_bg = span.style.bg if span.style.bg is not None else TuiColor.reset()
        if next_fg != fg or next_bg != bg:
            _set_fg_bg(writer, next_fg, next_bg)
            fg = next_fg
            bg = next_bg
        _print(writer, span.content)

    _set_fg_bg(writer, TuiColor.reset(), TuiColor.reset())
    _reset_colors(writer)


def write_history_line(
    writer: io.IOBase,
    line: HyperlinkLine,
    wrap_width: int,
) -> None:
    """Render a single wrapped history line (port of ``write_history_line``).

    In the Python port the per-line foreground/background style lives on
    ``line.line`` as a string prefix; we still clear continuation rows so
    terminal paint artifacts do not bleed through wrapped lines.
    """
    physical_rows = max(1, (line.width() + wrap_width - 1) // wrap_width)
    if physical_rows > 1:
        _save_position(writer)
        for _ in range(1, physical_rows):
            _move_down(writer, 1)
            _move_to_column(writer, 0)
            _clear_until_newline(writer)
        _restore_position(writer)
    _set_fg_bg(writer, TuiColor.reset(), TuiColor.reset())
    _clear_until_newline(writer)
    decorated = decorate_spans(line)
    writer.write("".join(decorated).encode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# leading_whitespace_prefix (string version, port one-to-one)
# ---------------------------------------------------------------------------

def leading_whitespace_prefix(line) -> str:
    """Return the leading whitespace of ``line`` (port of ``leading_whitespace_prefix``).

    Accepts either a plain string or a ratatui-style ``Line`` object
    (whose ``spans`` carry the content) — the Python port flattens
    ratatui lines to text before measuring the leading whitespace.
    """
    if isinstance(line, str):
        end = len(line)
        for idx, ch in enumerate(line):
            if not ch.isspace():
                end = idx
                break
        return line[:end]
    spans = getattr(line, "spans", None) or []
    out_parts: List[str] = []
    for span in spans:
        content = getattr(span, "content", str(span))
        for idx, ch in enumerate(content):
            if not ch.isspace():
                return "".join(out_parts) + content[:idx]
        out_parts.append(content)
    return "".join(out_parts)


# ---------------------------------------------------------------------------
# Terminal protocol + minimal implementation
# ---------------------------------------------------------------------------

@dataclass
class Size:
    width: int
    height: int


@dataclass
class CursorPos:
    x: int
    y: int


class BackendLike(Protocol):
    def size(self) -> Size: ...


class TerminalLike(Protocol):
    backend: Any
    backend_mut: io.IOBase
    viewport_area: Any
    last_known_cursor_pos: CursorPos

    def set_viewport_area(self, area: Any) -> None: ...
    def note_history_rows_inserted(self, n: int) -> None: ...
    def clear_after_position(self, position: Any) -> None: ...


def insert_history_lines(terminal: TerminalLike, lines: List[Line]) -> None:
    """Insert ``lines`` above the viewport (port of ``insert_history_lines``)."""
    return insert_history_lines_with_wrap_policy(
        terminal, lines, HistoryLineWrapPolicy.PreWrap
    )


def insert_history_lines_with_wrap_policy(
    terminal: TerminalLike,
    lines: List[Line],
    wrap_policy: HistoryLineWrapPolicy,
) -> None:
    """Port of ``insert_history_lines_with_wrap_policy``."""
    return insert_history_lines_with_mode_and_wrap_policy(
        terminal, lines, InsertHistoryMode.Standard, wrap_policy
    )


def insert_history_lines_with_mode_and_wrap_policy(
    terminal: TerminalLike,
    lines: List[Line],
    mode: InsertHistoryMode,
    wrap_policy: HistoryLineWrapPolicy,
) -> None:
    """Port of ``insert_history_lines_with_mode_and_wrap_policy``.

    The original Rust function accepts ``Vec<Line<'_>>``; the Python
    port flattens the ratatui-style ``Line`` (spans + style) to plain
    strings before passing them to :func:`plain_hyperlink_lines` so the
    rest of the TUI pipeline (which carries styles inside the ANSI
    string) keeps working.
    """
    return insert_history_hyperlink_lines_with_mode_and_wrap_policy(
        terminal,
        plain_hyperlink_lines([line_to_static(line) for line in lines]),
        mode,
        wrap_policy,
    )


def insert_history_hyperlink_lines_with_mode_and_wrap_policy(
    terminal: TerminalLike,
    lines: List[HyperlinkLine],
    mode: InsertHistoryMode,
    wrap_policy: HistoryLineWrapPolicy,
) -> None:
    """Port of ``insert_history_hyperlink_lines_with_mode_and_wrap_policy``."""
    try:
        screen_size = terminal.backend.size()
    except Exception:
        screen_size = Size(0, 0)

    area = terminal.viewport_area
    should_update_area = False
    last_cursor_pos = terminal.last_known_cursor_pos

    wrap_width = max(1, int(getattr(area, "width", 1)))
    wrapped: List[HyperlinkLine] = []
    wrapped_rows = 0
    for line in lines:
        if wrap_policy is HistoryLineWrapPolicy.Terminal:
            line_wrapped = [line]
        elif line_contains_url_like(line.line) and not line_has_mixed_url_and_non_url_tokens(
            line.line
        ):
            line_wrapped = [line]
        else:
            wrapped_raw = adaptive_wrap_line(
                line.line,
                RtOptions(wrap_width).with_subsequent_indent(leading_whitespace_prefix(line.line)),
            )
            wrapped_static = [line_to_static(l) for l in wrapped_raw]
            line_wrapped = remap_wrapped_line(line, wrapped_static)
        wrapped_rows += sum(
            max(1, (wrapped_line.width() + wrap_width - 1) // wrap_width)
            for wrapped_line in line_wrapped
        )
        wrapped.extend(line_wrapped)
    wrapped_lines = wrapped_rows
    if mode is InsertHistoryMode.ZellijRaw:
        try:
            terminal.clear_after_position(getattr(area, "as_position", lambda: None)())
        except Exception:
            pass
        writer = terminal.backend_mut
        _move_to(writer, 0, int(getattr(area, "top", 0)))
        for index, line in enumerate(wrapped):
            if index > 0:
                writer.write(b"\r\n")
            write_history_line(writer, line, wrap_width)
        area_height = int(getattr(area, "height", 0))
        for _ in range(area_height):
            writer.write(b"\r\n")
            _clear_until_newline(writer)
        _move_to(writer, last_cursor_pos.x, last_cursor_pos.y)

        viewport_top = min(
            int(getattr(area, "top", 0)) + wrapped_lines,
            screen_size.height - int(getattr(area, "height", 0)),
        )
        if int(getattr(area, "y", 0)) != viewport_top:
            try:
                area.y = viewport_top
            except Exception:
                pass
            should_update_area = True
    else:
        writer = terminal.backend_mut
        area_top = int(getattr(area, "top", 0))
        area_bottom = int(getattr(area, "bottom", lambda: area_top + int(getattr(area, "height", 0)))())
        if area_bottom < screen_size.height:
            scroll_amount = min(wrapped_lines, screen_size.height - area_bottom)
            top_1based = area_top + 1
            _set_scroll_region(writer, top_1based, screen_size.height)
            _move_to(writer, 0, area_top)
            _reverse_index(writer, scroll_amount)
            _reset_scroll_region(writer)
            cursor_top = max(0, area_top - 1)
            try:
                area.y = int(getattr(area, "y", 0)) + scroll_amount
            except Exception:
                pass
            should_update_area = True
        else:
            cursor_top = max(0, area_top - 1)
        _set_scroll_region(writer, 1, area_top)
        _move_to(writer, 0, cursor_top)
        for line in wrapped:
            writer.write(b"\r\n")
            write_history_line(writer, line, wrap_width)
        _reset_scroll_region(writer)
        _move_to(writer, last_cursor_pos.x, last_cursor_pos.y)

    if should_update_area:
        try:
            terminal.set_viewport_area(area)
        except Exception:
            pass
    if wrapped_lines > 0:
        try:
            terminal.note_history_rows_inserted(wrapped_lines)
        except Exception:
            pass


__all__ = [
    "CursorPos",
    "HistoryLineWrapPolicy",
    "InsertHistoryMode",
    "ModifierDiff",
    "Size",
    "TerminalLike",
    "insert_history_lines",
    "insert_history_lines_with_wrap_policy",
    "insert_history_lines_with_mode_and_wrap_policy",
    "insert_history_hyperlink_lines_with_mode_and_wrap_policy",
    "leading_whitespace_prefix",
    "write_history_line",
    "write_spans",
]
