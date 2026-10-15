"""Canonical pipe-table structure detection and fenced-code-block tracking.

Python port of codex-rs/tui/src/table_detect.rs (production code only;
Rust test-only helpers are intentionally not ported as part of this module).

Both the streaming controller and the markdown-fence unwrapper
(``markdown.py``) need to identify pipe-table structure and fenced code
blocks in raw markdown source. This module provides the canonical
implementations so fixes only need to happen in one place.
"""
from __future__ import annotations

from typing import List, Optional, Tuple


# -- Pipe-table detection (port of parse_table_segments) ----------------------

def parse_table_segments(line: str) -> Optional[List[str]]:
    """Split a pipe-delimited line into trimmed segments.

    Returns ``None`` if the line is empty or has no unescaped separator
    marker. Leading/trailing pipes are stripped before splitting.

    This is intentionally a structural parser, not a renderer. It
    preserves escaped pipes inside the returned segments because
    callers only care about whether the line can participate in a
    table, not how the cell text should finally be displayed.

    Port of ``codex::table_detect::parse_table_segments``.
    """
    trimmed = line.strip()
    if not trimmed:
        return None

    has_outer_pipe = trimmed.startswith("|") or trimmed.endswith("|")
    content = trimmed[1:] if trimmed.startswith("|") else trimmed
    content = content[:-1] if content.endswith("|") else content
    raw_segments = _split_unescaped_pipe(content)
    if not has_outer_pipe and len(raw_segments) <= 1:
        return None

    segments: List[str] = [s.strip() for s in raw_segments]
    return segments if segments else None


def _split_unescaped_pipe(content: str) -> List[str]:
    """Split ``content`` on unescaped ``|`` characters.

    A pipe preceded by ``\\`` is treated as literal text, not a column
    separator. The backslash remains in the segment (this is structure
    detection, not rendering).

    Port of ``codex::table_detect::split_unescaped_pipe``.
    """
    segments: List[str] = []
    start = 0
    i = 0
    while i < len(content):
        ch = content[i]
        if ch == "\\":
            i += 2
        elif ch == "|":
            segments.append(content[start:i])
            start = i + 1
            i += 1
        else:
            i += 1
    segments.append(content[start:])
    return segments


# -- Inline table-detection helpers (hot path) ---------------------------------

def is_table_header_line(line: str) -> bool:
    """Whether ``line`` looks like a table header row.

    Port of ``codex::table_detect::is_table_header_line``.
    """
    segments = parse_table_segments(line)
    return segments is not None and any(s for s in segments)


def _is_table_delimiter_segment(segment: str) -> bool:
    """Whether a single segment matches alignment-colon syntax.

    Port of ``codex::table_detect::is_table_delimiter_segment`` (private).
    """
    trimmed = segment.strip()
    if not trimmed:
        return False
    without_leading = trimmed[1:] if trimmed.startswith(":") else trimmed
    without_ends = without_leading[:-1] if without_leading.endswith(":") else without_leading
    return len(without_ends) >= 3 and all(c == "-" for c in without_ends)


def is_table_delimiter_line(line: str) -> bool:
    """Whether ``line`` is a valid table delimiter row.

    Port of ``codex::table_detect::is_table_delimiter_line``.
    """
    segments = parse_table_segments(line)
    return segments is not None and all(_is_table_delimiter_segment(s) for s in segments)


# -- Fenced code block tracking (port of FenceKind / FenceTracker) -------------

class FenceKind:
    """Where a source line sits relative to fenced code blocks.

    Table holdback only applies to lines that are ``Outside`` or inside
    a ``Markdown`` fence. Lines inside ``Other`` fences (e.g. ``sh``,
    ``rust``) are ignored by the table scanner because their pipe
    characters are code, not table syntax.

    Port of ``codex::table_detect::FenceKind``.
    """
    Outside = "outside"
    Markdown = "markdown"
    Other = "other"


class FenceTracker:
    """Incremental tracker for fenced-code-block open/close transitions.

    Feed lines one at a time via ``advance``; query the current context
    with ``kind``. The tracker handles leading-whitespace limits
    (>3 spaces → not a fence), blockquote prefix stripping, and
    backtick/tilde marker matching.

    The tracker reports the fence context that applies to the
    current line before that line mutates the state. Callers rely on
    that when deciding whether the current raw line can open or
    continue a table.

    Port of ``codex::table_detect::FenceTracker``.
    """

    def __init__(self) -> None:
        self._state: Optional[Tuple[str, int, str]] = None

    def advance(self, raw_line: str) -> None:
        """Process one raw source line and update fence state.

        Lines with >3 leading spaces are ignored (indented code blocks,
        not fences). Blockquote prefixes (``>``) are stripped before
        scanning.

        Port of ``FenceTracker::advance``.
        """
        leading_spaces = 0
        for ch in raw_line:
            if ch == " ":
                leading_spaces += 1
            else:
                break
        if leading_spaces > 3:
            return

        trimmed = raw_line[leading_spaces:]
        fence_scan_text = strip_blockquote_prefix(trimmed)
        marker_info = parse_fence_marker(fence_scan_text)
        if marker_info is None:
            return
        marker, length = marker_info
        if self._state is not None:
            open_char, open_len, _ = self._state
            if (
                marker == open_char
                and length >= open_len
                and not fence_scan_text[length:].strip()
            ):
                self._state = None
        else:
            kind = (
                FenceKind.Markdown
                if is_markdown_fence_info(fence_scan_text, length)
                else FenceKind.Other
            )
            self._state = (marker, length, kind)

    def kind(self) -> str:
        """Current fence context for the most-recently-advanced line.

        Port of ``FenceTracker::kind``.
        """
        if self._state is None:
            return FenceKind.Outside
        return self._state[2]


# -- Fence helper functions (port of parse_fence_marker et al.) ---------------

def parse_fence_marker(line: str) -> Optional[Tuple[str, int]]:
    """Return fence marker character and run length for a potential fence line.

    Recognises backtick and tilde fences with a minimum run of 3.
    The input should already have leading whitespace and blockquote
    prefixes stripped.

    Port of ``codex::table_detect::parse_fence_marker``.
    """
    if not line:
        return None
    first = line[0]
    if first not in ("`", "~"):
        return None
    length = 0
    for ch in line:
        if ch == first:
            length += 1
        else:
            break
    if length < 3:
        return None
    return (first, length)


def is_markdown_fence_info(trimmed_line: str, marker_len: int) -> bool:
    """Whether the info string after a fence marker indicates markdown content.

    Matches ``md`` and ``markdown`` (case-insensitive).

    Port of ``codex::table_detect::is_markdown_fence_info``.
    """
    info = trimmed_line[marker_len:].split()[0] if trimmed_line[marker_len:].split() else ""
    return info.lower() in ("md", "markdown")


def strip_blockquote_prefix(line: str) -> str:
    """Peel all leading ``>`` blockquote markers from a line.

    Tables can appear inside blockquotes (``> | A | B |``), so the
    holdback scanner must strip these markers before checking for
    table syntax.

    Port of ``codex::table_detect::strip_blockquote_prefix``.
    """
    rest = line.lstrip()
    while True:
        if not rest.startswith(">"):
            return rest
        rest = rest[1:]
        if rest.startswith(" "):
            rest = rest[1:]
        rest = rest.lstrip()


__all__ = [
    "FenceKind",
    "FenceTracker",
    "is_markdown_fence_info",
    "is_table_delimiter_line",
    "is_table_header_line",
    "parse_fence_marker",
    "parse_table_segments",
    "strip_blockquote_prefix",
]
