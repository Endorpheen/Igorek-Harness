"""Markdown-to-rendered-lines entry points.

Python port of codex-rs/tui/src/markdown.rs (production code only;
Rust test-only helpers are intentionally not ported as part of this module — but the ``append_markdown_agent`` function, while
``#[cfg(test)]`` in Rust, is ported as production code in Python
because the codex doc-comment notes that stream controllers use it
to render agent responses with markdown-fence unwrapping).

This module provides the public API surface that the rest of the TUI
uses to turn markdown source into rendered line lists. Two variants
exist:

- ``append_markdown`` — general-purpose, used for plan blocks and
  history cells that already hold pre-processed markdown (no fence
  unwrapping).
- ``append_markdown_agent`` / ``render_markdown_agent_with_links_and_cwd``
  — for agent responses. Runs ``unwrap_markdown_fences`` first so that
  ``\u0060\u0060\u0060md``/``\u0060\u0060\u0060markdown`` fences containing tables
  are stripped and markdown sees raw table syntax instead of fenced code.
"""
from __future__ import annotations

from typing import List, Optional

from .markdown_render import (
    render_markdown_lines_with_width_and_cwd,
    render_markdown_text_with_width_and_cwd,
)
from .table_detect import (
    is_markdown_fence_info,
    is_table_delimiter_line,
    is_table_header_line,
    parse_fence_marker,
    strip_blockquote_prefix,
)
from .terminal_hyperlinks import HyperlinkLine


# -- Public API (port of append_markdown / append_markdown_agent) -------------

def append_markdown(
    markdown_source: str,
    width: Optional[int],
    cwd: Optional[str],
    lines: List[str],
) -> None:
    """Render markdown source to rendered lines and append them to ``lines``.

    Callers that already know the session working directory should pass
    it here so streamed and non-streamed rendering show the same
    relative path text even if the process cwd differs.

    Port of ``codex::markdown::append_markdown``.
    """
    rendered = render_markdown_text_with_width_and_cwd(
        markdown_source, width, cwd
    )
    lines.extend(rendered)


def append_markdown_agent(
    markdown_source: str,
    width: Optional[int],
    lines: List[str],
) -> None:
    """Render an agent message to rendered lines.

    Before rendering, the source is passed through
    ``unwrap_markdown_fences`` so that tables wrapped in ``\u0060\u0060\u0060md``
    fences are rendered as native tables rather than code blocks.
    Non-markdown fences (e.g. ``rust``, ``sh``) are left intact.

    Port of ``codex::markdown::append_markdown_agent`` (production in
    Python because stream controllers rely on it; ``#[cfg(test)]`` in
    Rust reflects codex's test-driven re-export pattern).
    """
    normalized = unwrap_markdown_fences(markdown_source)
    rendered = render_markdown_text_with_width_and_cwd(
        normalized, width, None
    )
    lines.extend(rendered)


def render_markdown_agent_with_links_and_cwd(
    markdown_source: str,
    width: Optional[int],
    cwd: Optional[str],
) -> List[HyperlinkLine]:
    """Render agent markdown with hyperlink annotations.

    Port of ``codex::markdown::render_markdown_agent_with_links_and_cwd``.
    """
    normalized = unwrap_markdown_fences(markdown_source)
    return render_markdown_lines_with_width_and_cwd(normalized, width, cwd)


# -- Markdown-fence unwrapper (port of unwrap_markdown_fences) ----------------

def unwrap_markdown_fences(markdown_source: str) -> str:
    """Strip ``\u0060\u0060\u0060md``/``\u0060\u0060\u0060markdown`` fences that contain tables.

    Emitting their content as bare markdown so markdown parses the
    tables natively.

    Fences whose info string is not ``md`` or ``markdown`` are passed
    through unchanged. Markdown fences that do *not* contain a table
    (detected by checking for a header row + delimiter row) are also
    passed through so that non-table markdown inside a fence still
    renders as a code block.

    The fence unwrapping is intentionally conservative: it buffers the
    entire fence body before deciding, and an unclosed fence at
    end-of-input is re-emitted with its opening line so partial
    streams degrade to code display.

    Port of ``codex::markdown::unwrap_markdown_fences``.
    """
    # Zero-copy fast path: most messages contain no fences at all.
    if "\u0060\u0060\u0060" not in markdown_source and "~~~" not in markdown_source:
        return markdown_source

    class Fence:
        """Port of the Rust Fence struct (marker, length, is_blockquoted)."""
        __slots__ = ("marker", "length", "is_blockquoted")

        def __init__(self, marker: str, length: int, is_blockquoted: bool) -> None:
            self.marker = marker
            self.length = length
            self.is_blockquoted = is_blockquoted

    def _strip_line_indent(line: str) -> Optional[str]:
        """Strip trailing newline + up to 3 leading spaces; return trimmed slice.

        Returns ``None`` when the line has 4+ leading spaces (indented
        code line per CommonMark).
        """
        if line.endswith("\n"):
            without_newline = line[:-1]
        else:
            without_newline = line
        byte_idx = 0
        column = 0
        for ch in without_newline:
            if ch == " ":
                byte_idx += 1
                column += 1
            elif ch == "\t":
                byte_idx += 1
                column = column + 4 - column % 4 if column % 4 else column + 4
            else:
                break
            if column >= 4:
                return None
        return without_newline[byte_idx:]

    def _parse_open_fence(line: str) -> Optional[tuple]:
        """Parse an opening fence line, returning ``(Fence, is_markdown)``."""
        trimmed = _strip_line_indent(line)
        if trimmed is None:
            return None
        is_blockquoted = trimmed.lstrip().startswith(">")
        fence_scan_text = strip_blockquote_prefix(trimmed)
        marker_info = parse_fence_marker(fence_scan_text)
        if marker_info is None:
            return None
        marker, length = marker_info
        is_markdown = is_markdown_fence_info(fence_scan_text, length)
        return (
            Fence(marker, length, is_blockquoted),
            is_markdown,
        )

    def _is_close_fence(line: str, fence: Fence) -> bool:
        trimmed = _strip_line_indent(line)
        if trimmed is None:
            return False
        if fence.is_blockquoted:
            if not trimmed.lstrip().startswith(">"):
                return False
            fence_scan_text = strip_blockquote_prefix(trimmed)
        else:
            fence_scan_text = trimmed
        marker_info = parse_fence_marker(fence_scan_text)
        if marker_info is None:
            return False
        marker, length = marker_info
        return (
            marker == fence.marker
            and length >= fence.length
            and not fence_scan_text[length:].strip()
        )

    def _markdown_fence_contains_table(content: str, is_blockquoted_fence: bool) -> bool:
        previous_line: Optional[str] = None
        for line in content.splitlines():
            text = (
                strip_blockquote_prefix(line) if is_blockquoted_fence else line
            )
            trimmed = text.strip()
            if not trimmed:
                previous_line = None
                continue
            if (
                previous_line is not None
                and is_table_header_line(previous_line)
                and not is_table_delimiter_line(previous_line)
                and is_table_delimiter_line(trimmed)
            ):
                return True
            previous_line = trimmed
        return False

    class MarkdownCandidateData:
        """Port of Rust ``MarkdownCandidateData`` (fence + opening/content ranges)."""
        __slots__ = ("fence", "opening_start", "opening_end", "content_ranges")

        def __init__(self, fence: Fence, opening_start: int, opening_end: int) -> None:
            self.fence = fence
            self.opening_start = opening_start
            self.opening_end = opening_end
            self.content_ranges: List[tuple] = []

    class ActiveFence:
        """Port of Rust ``ActiveFence`` enum (boxed to keep small variant cheap)."""
        pass

    class PassthroughFence(ActiveFence):
        __slots__ = ("fence",)
        def __init__(self, fence: Fence) -> None:
            self.fence = fence

    class MarkdownCandidate(ActiveFence):
        __slots__ = ("data",)
        def __init__(self, data: MarkdownCandidateData) -> None:
            self.data = data

    out_parts: List[str] = []
    active_fence: Optional[ActiveFence] = None
    source_offset = 0

    def _push_source_range(start: int, end: int) -> None:
        if end > start:
            out_parts.append(markdown_source[start:end])

    for line in markdown_source.splitlines(True):
        line_start = source_offset
        source_offset += len(line)
        line_end = source_offset

        if active_fence is not None:
            current = active_fence
            active_fence = None
            if isinstance(current, PassthroughFence):
                _push_source_range(line_start, line_end)
                if not _is_close_fence(line, current.fence):
                    active_fence = PassthroughFence(current.fence)
            elif isinstance(current, MarkdownCandidate):
                data = current.data
                if _is_close_fence(line, data.fence):
                    content = "".join(
                        markdown_source[s:e] for s, e in data.content_ranges
                    )
                    if _markdown_fence_contains_table(content, data.fence.is_blockquoted):
                        for s, e in data.content_ranges:
                            _push_source_range(s, e)
                    else:
                        _push_source_range(data.opening_start, data.opening_end)
                        for s, e in data.content_ranges:
                            _push_source_range(s, e)
                        _push_source_range(line_start, line_end)
                else:
                    data.content_ranges.append((line_start, line_end))
                    active_fence = MarkdownCandidate(data)
            continue

        parsed = _parse_open_fence(line)
        if parsed is not None:
            fence, is_markdown = parsed
            if is_markdown:
                active_fence = MarkdownCandidate(
                    MarkdownCandidateData(fence, line_start, line_end)
                )
            else:
                _push_source_range(line_start, line_end)
                active_fence = PassthroughFence(fence)
            continue

        _push_source_range(line_start, line_end)

    if isinstance(active_fence, MarkdownCandidate):
        data = active_fence.data
        _push_source_range(data.opening_start, data.opening_end)
        for s, e in data.content_ranges:
            _push_source_range(s, e)
    # PassthroughFence at end-of-input is intentionally dropped (Rust does the same).

    return "".join(out_parts)


__all__ = [
    "append_markdown",
    "append_markdown_agent",
    "render_markdown_agent_with_links_and_cwd",
    "unwrap_markdown_fences",
]
