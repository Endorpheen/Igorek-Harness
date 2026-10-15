"""Line truncation helpers (port of codex-rs/tui/src/line_truncation.rs).

In our ANSI-string world ``Line`` is a plain ``str`` (with embedded ANSI
codes) and ``Span`` is a (style, text) tuple. Width is measured as the
display width of the text with ANSI codes stripped.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .terminal_hyperlinks import _char_width


Line = str
Span = Tuple[str, str]  # (style-prefix, text) — or just text in our simplified model
Style = str  # ANSI prefix string


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _display_width(text: str) -> int:
    """Display width of a string with ANSI codes stripped."""
    return len(_ANSI_RE.sub("", text))


def line_width(line: Line) -> int:
    """Compute the display width of a (potentially multi-span) line.

    Port of ``codex::line_truncation::line_width``.

    Note: in the simplified Python world a line is a single ``str``;
    callers that need per-span widths can split on the literal ``\\x1b[``
    markers first.
    """
    return _display_width(line)


def truncate_line_to_width(line: Line, max_width: int) -> Line:
    """Truncate a styled line to ``max_width`` display columns.

    Port of ``codex::line_truncation::truncate_line_to_width``.
    """
    if max_width == 0:
        return ""

    plain = _ANSI_RE.sub("", line)
    if len(plain) == 0:
        return ""

    out: List[str] = []
    used = 0
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\x1b":
            m = _ANSI_RE.match(line, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
        width = _char_width(ch)
        if used + width > max_width:
            break
        out.append(ch)
        used += width
        i += 1
    return "".join(out)


def truncate_line_with_ellipsis_if_overflow(line: Line, max_width: int) -> Line:
    """Truncate a styled line to ``max_width`` and append an ellipsis on overflow.

    Port of ``codex::line_truncation::truncate_line_with_ellipsis_if_overflow``.
    """
    if max_width == 0:
        return ""
    if _display_width(line) <= max_width:
        return line
    truncated = truncate_line_to_width(line, max(0, max_width - 1))
    return truncated + "\u2026"


__all__ = [
    "line_width",
    "truncate_line_to_width",
    "truncate_line_with_ellipsis_if_overflow",
]
