"""Text formatting helpers (port of codex-rs/tui/src/text_formatting.rs).

In the Python port, line/span abstraction is collapsed: lines are
``str`` and grapheme handling is delegated to ``grapheme``/``unicodedata``
where needed.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import List, Optional, Sequence, TypeVar

from .terminal_hyperlinks import _char_width


T = TypeVar("T", bound="str")


# -- capitalize_first (port one-to-one) ---------------------------------------------

def capitalize_first(input: str) -> str:
    """Uppercase the first character of ``input`` (port of ``capitalize_first``)."""
    if not input:
        return ""
    first = input[0]
    rest = input[1:]
    return first.upper() + rest


# -- format_and_truncate_tool_result (port one-to-one) ----------------------------

def format_and_truncate_tool_result(
    text: str, max_lines: int, line_width: int
) -> str:
    """Truncate a tool result to fit within ``max_lines * line_width`` graphemes.

    Port of ``codex::text_formatting::format_and_truncate_tool_result``.
    """
    max_graphemes = max(0, max_lines * line_width - max_lines)
    formatted = format_json_compact(text)
    if formatted is not None:
        return truncate_text(formatted, max_graphemes)
    return truncate_text(text, max_graphemes)


# -- format_json_compact (port one-to-one) -----------------------------------------

def format_json_compact(text: str) -> Optional[str]:
    """Format JSON text in a compact single-line format with spaces.

    Ex: ``{"a":"b",c:["d","e"]}`` -> ``{"a": "b", "c": ["d", "e"]}``.

    Returns ``None`` if the input is not valid JSON.

    Port of ``codex::text_formatting::format_json_compact``.
    """
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
    if pretty is None:
        return None

    out: List[str] = []
    in_string = False
    escape_next = False
    chars = list(pretty)
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch == '"' and not escape_next:
            in_string = not in_string
            out.append(ch)
        elif ch == "\\" and in_string:
            escape_next = not escape_next
            out.append(ch)
        elif ch in ("\n", "\r") and not in_string:
            pass
        elif ch in (" ", "\t") and not in_string:
            if out and out[-1] in (":", ",") and i + 1 < len(chars) and chars[i + 1] not in ("}", "]"):
                out.append(" ")
        else:
            if escape_next and in_string:
                escape_next = False
            out.append(ch)
        i += 1
    return "".join(out)


# -- truncate_text (port one-to-one) ----------------------------------------------

def truncate_text(text: str, max_graphemes: int) -> str:
    """Truncate ``text`` to ``max_graphemes`` graphemes.

    Port of ``codex::text_formatting::truncate_text``.
    """
    if max_graphemes == 0:
        return ""
    graphemes = list(_iter_graphemes(text))
    if len(graphemes) <= max_graphemes:
        return text
    if max_graphemes >= 3:
        truncated = "".join(graphemes[: max_graphemes - 3])
        return f"{truncated}..."
    return "".join(graphemes[:max_graphemes])


def _iter_graphemes(text: str):
    """Iterate over Unicode grapheme clusters (port one-to-one — port of unicode_segmentation)."""
    try:
        import regex
        return regex.findall(r"\X", text)
    except ImportError:
        # Fallback: simple per-codepoint iteration. Not strictly grapheme-correct
        # for compound emoji or combining sequences, but matches the
        # `text.chars()` fallback Rust uses when unicode_segmentation is unavailable.
        return list(text)


# -- center_truncate_path (port one-to-one) ---------------------------------------

def center_truncate_path(path: str, max_width: int) -> str:
    """Truncate a path-like string to ``max_width`` display columns.

    Keeps leading and trailing segments where possible and inserts a
    Unicode ellipsis between them. If an individual segment cannot
    fit, it is front-truncated with an ellipsis.

    Port of ``codex::text_formatting::center_truncate_path``.
    """
    if max_width == 0:
        return ""
    if _display_width(path) <= max_width:
        return path

    sep = os.sep
    has_leading_sep = path.startswith(sep)
    has_trailing_sep = path.endswith(sep) and len(path) > 1
    raw_segments = path.split(sep)
    if has_leading_sep and raw_segments and raw_segments[0] == "":
        raw_segments = raw_segments[1:]
    if has_trailing_sep and raw_segments and raw_segments[-1] == "":
        raw_segments = raw_segments[:-1]

    if not raw_segments:
        if has_leading_sep:
            if _display_width(sep) <= max_width:
                return sep
        return "\u2026"

    class Segment:
        __slots__ = ("original", "text", "truncatable", "is_suffix")

        def __init__(self, original: str, truncatable: bool, is_suffix: bool) -> None:
            self.original = original
            self.text = original
            self.truncatable = truncatable
            self.is_suffix = is_suffix

    def assemble(leading: bool, segments: Sequence[Segment]) -> str:
        result: List[str] = []
        if leading:
            result.append(sep)
        for seg in segments:
            if result and not result[-1].endswith(sep):
                result.append(sep)
            result.append(seg.text)
        return "".join(result)

    def front_truncate(original: str, allowed_width: int) -> str:
        if allowed_width == 0:
            return ""
        if _display_width(original) <= allowed_width:
            return original
        if allowed_width == 1:
            return "\u2026"
        kept: List[str] = []
        used_width = 1
        for ch in reversed(original):
            ch_width = _char_width(ch)
            if used_width + ch_width > allowed_width:
                break
            used_width += ch_width
            kept.append(ch)
        kept.reverse()
        return "\u2026" + "".join(kept)

    segment_count = len(raw_segments)
    combos: List[tuple] = []
    for left in range(1, segment_count + 1):
        min_right = 0 if left == segment_count else 1
        for right in range(min_right, segment_count - left + 1):
            combos.append((left, right))
    desired_suffix = min(2, segment_count - 1) if segment_count > 1 else 0
    prioritized = [c for c in combos if c[1] >= desired_suffix]
    fallback = [c for c in combos if c[1] < desired_suffix]

    def sort_combos(items: List[tuple]) -> None:
        items.sort(key=lambda x: (-x[0], -x[1], -(x[0] + x[1])))

    sort_combos(prioritized)
    sort_combos(fallback)

    def fit_segments(segments: List[Segment], allow_front_truncate: bool) -> Optional[str]:
        while True:
            candidate = assemble(has_leading_sep, segments)
            width = _display_width(candidate)
            if width <= max_width:
                return candidate
            if not allow_front_truncate:
                return None
            indices: List[int] = []
            for idx, seg in reversed(list(enumerate(segments))):
                if seg.truncatable and seg.is_suffix:
                    indices.append(idx)
            for idx, seg in reversed(list(enumerate(segments))):
                if seg.truncatable and not seg.is_suffix:
                    indices.append(idx)
            if not indices:
                return None
            changed = False
            for idx in indices:
                original_width = _display_width(segments[idx].original)
                if original_width <= max_width and segment_count > 2:
                    continue
                seg_width = _display_width(segments[idx].text)
                other_width = width - seg_width
                allowed_width = max(1, max_width - other_width)
                new_text = front_truncate(segments[idx].original, allowed_width)
                if new_text != segments[idx].text:
                    segments[idx].text = new_text
                    changed = True
                    break
            if not changed:
                return None

    for left_count, right_count in prioritized + fallback:
        segments: List[Segment] = [
            Segment(seg, truncatable=True, is_suffix=False)
            for seg in raw_segments[:left_count]
        ]
        need_ellipsis = left_count + right_count < segment_count
        if need_ellipsis:
            segments.append(Segment("\u2026", truncatable=False, is_suffix=False))
        if right_count > 0:
            segments.extend(
                Segment(seg, truncatable=True, is_suffix=True)
                for seg in raw_segments[segment_count - right_count:]
            )
        allow_front_truncate = need_ellipsis or segment_count <= 2
        segs_copy = list(segments)
        if (result := fit_segments(segs_copy, allow_front_truncate)) is not None:
            return result

    return front_truncate(path, max_width)


# -- proper_join (port one-to-one) -------------------------------------------------

def proper_join(items: Sequence[T]) -> str:
    """Join a list of strings with proper English punctuation.

    Examples:
    - ``[]`` -> ``""``
    - ``["apple"]`` -> ``"apple"``
    - ``["apple", "banana"]`` -> ``"apple and banana"``
    - ``["apple", "banana", "cherry"]`` -> ``"apple, banana and cherry"``

    Port of ``codex::text_formatting::proper_join``.
    """
    if not items:
        return ""
    if len(items) == 1:
        return str(items[0])
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    last = items[-1]
    middle = ", ".join(str(i) for i in items[:-1])
    return f"{middle} and {last}"


# -- Display width helper (Python analogue of UnicodeWidthStr) ---------------

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _display_width(text: str) -> int:
    """Display width of a string with ANSI codes stripped (port one-to-one)."""
    return len(_ANSI_RE.sub("", text))


__all__ = [
    "capitalize_first",
    "center_truncate_path",
    "format_and_truncate_tool_result",
    "format_json_compact",
    "proper_join",
    "truncate_text",
]
