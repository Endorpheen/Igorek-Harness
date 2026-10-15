"""Screen-frame model and selection extraction for the managed TUI area.

Represent only the last frame actually drawn in the managed region: live output
and composer, not the complete terminal scrollback. Derive zones from render
structure (window/fragment metadata), never by searching displayed text for a
reasoning label or a collapsed-line marker such as ``… +N lines``.

Selection uses a linear character range, inclusive start and exclusive end.
Middle rows contribute their complete display text. Join rows with ``\\n``
without an artificial trailing newline or right-hand layout padding. Strip
ANSI/OSC sequences and preserve leading indentation. Map wide Unicode,
combining marks and tabs through terminal columns, not Python string indices.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

try:
    from wcwidth import wcwidth as _wcwidth
except Exception:  # pragma: no cover - wcwidth always present via prompt_toolkit
    def _wcwidth(ch: str) -> int:  # type: ignore
        return 1


_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_OTHER_RE = re.compile(r"\x1b[()][A-Za-z0-9]|\x1b[@-Z\\-_]")


def strip_ansi(text: str) -> str:
    if not text:
        return ""
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_CSI_RE.sub("", text)
    text = _ANSI_OTHER_RE.sub("", text)
    return text


def char_width(ch: str) -> int:
    w = _wcwidth(ch)
    if w is None:
        return 1
    return int(w)


def display_width(text: str) -> int:
    """Terminal column width of ``text`` (tabs expand to the next stop)."""
    col = 0
    for ch in text:
        if ch == "\t":
            col += 8 - (col % 8)
            continue
        w = char_width(ch)
        if w <= 0:
            continue
        col += w
    return col


def column_to_index(text: str, col: int) -> int:
    """Map a zero-based terminal column to the Python string index of the
    character occupying that column (start index). Wide characters map
    every covered column to the same character start. Returns ``len(text)``
    when the column is past the end."""
    if col <= 0:
        return 0
    current = 0
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if ch == "\t":
            width = 8 - (current % 8)
        else:
            width = char_width(ch)
        if width <= 0:
            index += 1
            continue
        if col < current + width:
            return index
        current += width
        index += 1
    return length


# ---------------------------------------------------------------------------
# Frame model
# ---------------------------------------------------------------------------

@dataclass
class FrameRow:
    screen_row: int
    ansi: str
    region_id: str = ""
    cell_id: str = ""
    expandable: bool = False
    expanded: bool = False
    header: bool = False

    @property
    def plain(self) -> str:
        return strip_ansi(self.ansi)


@dataclass
class FrameRegion:
    region_id: str
    kind: str
    cell_id: str = ""
    top: int = 0
    bottom: int = 0
    expandable: bool = False
    expanded: bool = False
    tail_row: Optional[int] = None
    header_row: Optional[int] = None

    def contains(self, row: int, col: int, col_end: Optional[int] = None) -> bool:
        if row < self.top or row > self.bottom:
            return False
        if col_end is None:
            return True
        return col < col_end


@dataclass
class ScreenFrame:
    revision: int = 0
    rows: list[FrameRow] = field(default_factory=list)
    regions: dict[str, FrameRegion] = field(default_factory=dict)
    composer_top: Optional[int] = None
    width: int = 0
    # Absolute transcript index of the first visible row, and the full
    # transcript lines. Populated in fullscreen so a drag selection can span
    # rows that are currently scrolled off-screen.
    window_start: int = 0
    full_lines: list[str] = field(default_factory=list)

    def row_map(self) -> dict[int, FrameRow]:
        return {row.screen_row: row for row in self.rows}

    def region_for_row(self, screen_row: int) -> Optional[FrameRegion]:
        fallback: Optional[FrameRegion] = None
        for region in self.regions.values():
            if region.top <= screen_row <= region.bottom:
                if region.kind in ("reasoning", "tool"):
                    return region
                if fallback is None:
                    fallback = region
        return fallback


@dataclass
class Selection:
    anchor_row: int
    anchor_col: int
    focus_row: int
    focus_col: int
    frame_revision: int
    owner: str = "app"

    def normalized(self) -> tuple[tuple[int, int], tuple[int, int]]:
        start = (self.anchor_row, self.anchor_col)
        end = (self.focus_row, self.focus_col)
        if start <= end:
            return start, end
        return end, start

    def is_empty(self) -> bool:
        return (self.anchor_row, self.anchor_col) == (self.focus_row, self.focus_col)


def _slice_row(text: str, start_col: Optional[int], end_col: Optional[int]) -> str:
    width = display_width(text)
    if start_col is None:
        start_col = 0
    if end_col is None:
        end_col = width
    start_col = max(0, min(start_col, width))
    end_col = max(0, min(end_col, width))
    if end_col < start_col:
        return ""
    start_idx = column_to_index(text, start_col)
    end_idx = column_to_index(text, end_col)
    if end_idx < start_idx:
        return ""
    return text[start_idx:end_idx]


def extract_selection(frame: ScreenFrame, selection: Selection) -> Optional[str]:
    """Extract the selected plain text or ``None`` if the range is invalid.

    Returns ``None`` when the selection is empty, when it crosses a row
    outside the managed frame, or when it covers no characters. Cross-row
    selections use ``\\n`` separators and never add a trailing newline.
    """
    if selection.is_empty():
        return None
    (start_row, start_col), (end_row, end_col) = selection.normalized()
    row_map = frame.row_map()

    # A selection may only cover rows that are present in the frame. Any
    # missing intermediate row means the gesture crossed an unknown area
    # and must be cancelled entirely.
    span = list(range(start_row, end_row + 1))
    if not span:
        return None
    rows: list[FrameRow] = []
    for r in span:
        row = row_map.get(r)
        if row is None:
            return None
        rows.append(row)

    parts: list[str] = []
    last_index = len(rows) - 1
    for i, row in enumerate(rows):
        text = row.plain
        if start_row == end_row:
            piece = _slice_row(text, start_col, end_col)
        elif i == 0:
            piece = _slice_row(text, start_col, None)
        elif i == last_index:
            piece = _slice_row(text, 0, end_col)
        else:
            piece = text
        parts.append(piece)

    result = "\n".join(parts)
    if result == "":
        return None
    return result


def extract_selection_absolute(
    full_lines: list[str],
    start_row: int,
    start_col: int,
    end_row: int,
    end_col: int,
) -> Optional[str]:
    """Extract a selection over absolute transcript rows (not just the window).

    Used when a fullscreen drag scrolls the transcript: the anchor/focus are
    kept in transcript coordinates so rows scrolled off-screen are still part
    of the copied text.
    """
    if not full_lines:
        return None
    if (start_row, start_col) > (end_row, end_col):
        start_row, start_col, end_row, end_col = end_row, end_col, start_row, start_col
    total = len(full_lines)
    if start_row < 0:
        start_row, start_col = 0, 0
    if end_row >= total:
        end_row = total - 1
        end_col = None  # type: ignore[assignment]
    elif end_row < 0:
        return None
    if start_row > end_row:
        return None

    parts: list[str] = []
    for r in range(start_row, end_row + 1):
        text = strip_ansi(full_lines[r])
        if start_row == end_row:
            piece = _slice_row(text, start_col, end_col)
        elif r == start_row:
            piece = _slice_row(text, start_col, None)
        elif r == end_row:
            piece = _slice_row(text, 0, end_col)
        else:
            piece = text
        parts.append(piece)

    result = "\n".join(parts)
    return result or None


def highlight_lines(lines: list[str], selection: Optional[Selection]) -> list[str]:
    """Return a copy of ``lines`` with the selected range reverse-videoed.

    Highlighting only affects managed rows, never changes the number of
    display columns, and is rebuilt on every frame (temporary by design).
    """
    if selection is None or selection.is_empty():
        return lines
    (start_row, start_col), (end_row, end_col) = selection.normalized()
    out = list(lines)
    for r in range(start_row, end_row + 1):
        if r < 0 or r >= len(out):
            continue
        plain = strip_ansi(out[r])
        width = display_width(plain)
        s = 0 if r != start_row else min(start_col, width)
        e = width if r != end_row else min(end_col, width)
        if e < s:
            s, e = e, s
        si = column_to_index(plain, s)
        ei = column_to_index(plain, e)
        if ei < si:
            si, ei = ei, si
        if ei <= si:
            continue
        out[r] = plain[:si] + "\x1b[7m" + plain[si:ei] + "\x1b[0m" + plain[ei:]
    return out


def merge_frame_rows(
    rows: Iterable[tuple[int, str, str, str, bool, bool]],
) -> list[FrameRow]:
    """Convenience helper to build ``FrameRow`` objects from tuples."""
    out: list[FrameRow] = []
    for screen_row, ansi, region_id, cell_id, expandable, expanded in rows:
        out.append(
            FrameRow(
                screen_row=int(screen_row),
                ansi=ansi,
                region_id=region_id,
                cell_id=cell_id,
                expandable=bool(expandable),
                expanded=bool(expanded),
            )
        )
    return out


__all__ = [
    "FrameRegion",
    "FrameRow",
    "ScreenFrame",
    "Selection",
    "char_width",
    "column_to_index",
    "display_width",
    "extract_selection",
    "extract_selection_absolute",
    "merge_frame_rows",
    "strip_ansi",
]
