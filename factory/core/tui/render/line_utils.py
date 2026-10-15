"""Line utilities — Python port of codex-rs/tui/src/render/line_utils.rs.

Codex uses ratatui's owned/borrowed ``Line`` distinction. Python has no
ownership — every string is already owned — so ``line_to_static`` and
``push_owned_lines`` collapse to trivial operations. The functional
mapping mirrors the Rust file 1:1.
"""
from __future__ import annotations

from typing import List


def line_to_static(line) -> str:
    """Clone a borrowed ratatui ``Line`` into an owned ``'static`` line.

    Port of ``codex::render::line_utils::line_to_static``.

    Accepts either a plain string (which is returned unchanged) or a
    ratatui-style ``Line`` object with ``.spans``. The Python port
    flattens the latter into the concatenated text of its spans.
    """
    if isinstance(line, str):
        return line
    spans = getattr(line, "spans", None)
    if spans is not None:
        return "".join(getattr(s, "content", str(s)) for s in spans)
    return str(line)


def push_owned_lines(src: List[str], out: List[str]) -> None:
    """Append owned copies of borrowed lines to ``out``.

    Port of ``codex::render::line_utils::push_owned_lines``.
    """
    out.extend(src)


def is_blank_line_spaces_only(line: str) -> bool:
    """Consider a line blank if it has no spans or only spans whose contents
    are empty or consist solely of spaces (no tabs/newlines).

    Port of ``codex::render::line_utils::is_blank_line_spaces_only``. In our
    ANSI-string world a line is one ``str``, so a line is "blank" if it's
    empty or all spaces.
    """
    if not line:
        return True
    return all(c == " " for c in line)


def prefix_lines(
    lines: List[str],
    initial_prefix: str,
    subsequent_prefix: str,
) -> List[str]:
    """Prefix each line with ``initial_prefix`` for the first line and
    ``subsequent_prefix`` for following lines. Returns a new list of owned
    lines.

    Port of ``codex::render::line_utils::prefix_lines``.
    """
    result: List[str] = []
    for i, line in enumerate(lines):
        prefix = initial_prefix if i == 0 else subsequent_prefix
        result.append(f"{prefix}{line}")
    return result


__all__ = [
    "line_to_static",
    "push_owned_lines",
    "is_blank_line_spaces_only",
    "prefix_lines",
]
