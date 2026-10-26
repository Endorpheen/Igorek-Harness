"""Incremental visual row wrapping (port of codex-rs/tui/src/live_wrap.rs).

The Python port uses :mod:`wcwidth` if available and falls back to a
``len(text)`` approximation otherwise, mirroring the unicode-width crate
the Rust source uses.

The test module from the source is **not** ported per the project's
"no tests" policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


try:
    from wcwidth import wcswidth, wcwidth  # type: ignore
    _HAVE_WCWIDTH = True
except Exception:
    _HAVE_WCWIDTH = False


def _char_width(ch: str) -> int:
    if not ch:
        return 0
    if _HAVE_WCWIDTH:
        w = wcwidth(ch)
        return w if w is not None and w >= 0 else 0
    return 1


def _str_width(text: str) -> int:
    if _HAVE_WCWIDTH:
        w = wcswidth(text)
        return w if w is not None and w >= 0 else len(text)
    return len(text)


@dataclass
class Row:
    """A single visual row produced by :class:`RowBuilder` (port one-to-one)."""
    text: str
    explicit_break: bool = False

    def width(self) -> int:
        return _str_width(self.text)


class RowBuilder:
    """Incrementally wraps input text into visual rows (port one-to-one)."""

    def __init__(self, target_width: int) -> None:
        self.target_width: int = max(1, target_width)
        self.current_line: str = ""
        self.rows: List[Row] = []

    def width(self) -> int:
        return self.target_width

    def set_width(self, width: int) -> None:
        """Update the target width and rewrap existing content (port one-to-one)."""
        self.target_width = max(1, width)
        all_text_parts: List[str] = []
        for row in self.rows:
            all_text_parts.append(row.text)
            if row.explicit_break:
                all_text_parts.append("\n")
        all_text_parts.append(self.current_line)
        all_text = "".join(all_text_parts)
        self.rows.clear()
        self.current_line = ""
        self.push_fragment(all_text)

    def push_fragment(self, fragment: str) -> None:
        """Push an input fragment that may contain newlines (port one-to-one)."""
        if not fragment:
            return
        start = 0
        i = 0
        for i, ch in enumerate(fragment):
            if ch == "\n":
                if start < i:
                    self.current_line += fragment[start:i]
                self.flush_current_line(explicit_break=True)
                start = i + len(ch.encode("utf-8"))
        if start < len(fragment):
            self.current_line += fragment[start:]
            self.wrap_current_line()

    def end_line(self) -> None:
        """Mark the end of the current logical line (port one-to-one)."""
        self.flush_current_line(explicit_break=True)

    def rows_snapshot(self) -> List[Row]:
        """Return a snapshot of produced rows (non-draining, port one-to-one)."""
        return list(self.rows)

    def display_rows(self) -> List[Row]:
        """Return rows suitable for display, including the partial line (port one-to-one)."""
        out = list(self.rows)
        if self.current_line:
            out.append(Row(text=self.current_line, explicit_break=False))
        return out

    def drain_commit_ready(self, max_keep: int) -> List[Row]:
        """Drain the oldest rows that exceed ``max_keep`` display rows (port one-to-one)."""
        display_count = len(self.rows) + (0 if not self.current_line else 1)
        if display_count <= max_keep:
            return []
        to_commit = display_count - max_keep
        commit_count = min(to_commit, len(self.rows))
        drained: List[Row] = []
        for _ in range(commit_count):
            drained.append(self.rows.pop(0))
        return drained

    def flush_current_line(self, explicit_break: bool) -> None:
        """Wrap any remaining content and finalize with ``explicit_break`` (port one-to-one)."""
        self.wrap_current_line()
        if explicit_break:
            if not self.current_line:
                self.rows.append(Row(text="", explicit_break=True))
            else:
                s = self.current_line
                self.current_line = ""
                self.rows.append(Row(text=s, explicit_break=True))
        self.current_line = ""

    def wrap_current_line(self) -> None:
        """Wrap ``current_line`` while it exceeds ``target_width`` (port one-to-one)."""
        while self.current_line:
            prefix, suffix, _taken = take_prefix_by_width(self.current_line, self.target_width)
            if not _taken:
                first = next(
                    (
                        (i, ch)
                        for i, ch in enumerate(self.current_line)
                    ),
                    None,
                )
                if first is None:
                    break
                i, ch = first
                end = i + 1
                p = self.current_line[:end]
                self.rows.append(Row(text=p, explicit_break=False))
                self.current_line = self.current_line[end:]
                continue
            if not suffix:
                break
            self.rows.append(Row(text=prefix, explicit_break=False))
            self.current_line = suffix


def take_prefix_by_width(
    text: str,
    max_cols: int,
) -> Tuple[str, str, int]:
    """Take a prefix of ``text`` whose visible width is at most ``max_cols``.

    Port of ``codex::live_wrap::take_prefix_by_width``.
    """
    if max_cols == 0 or not text:
        return ("", text, 0)
    cols = 0
    end_idx = 0
    for i, ch in enumerate(text):
        ch_w = _char_width(ch)
        if cols + ch_w > max_cols:
            break
        cols += ch_w
        end_idx = i + 1
    prefix = text[:end_idx]
    suffix = text[end_idx:]
    return prefix, suffix, cols


__all__ = [
    "Row",
    "RowBuilder",
    "take_prefix_by_width",
]
