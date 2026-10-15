"""Semantic terminal hyperlinks carried separately from visible TUI text.

Python port of codex-rs/tui/src/terminal_hyperlinks.rs. Layout code
measures and wraps ordinary lines; hyperlink annotations are applied
only when text reaches a terminal buffer or scrollback writer so OSC 8
bytes never affect geometry.

In our ANSI-string world ``HyperlinkLine`` collapses the styled line
into a single ``str`` and tracks hyperlinks as a parallel list of
``(column_range, destination)`` tuples (the Python analogue of
``TerminalHyperlink``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from .render.highlight import Style
from .render.line_utils import line_to_static as _line_to_static
from .wrapping import (
    RtOptions,
    adaptive_wrap_line,
)


# -- Hyperlink types (port of TerminalHyperlink / HyperlinkLine) ---------------

@dataclass
class TerminalHyperlink:
    """Port of ``codex::terminal_hyperlinks::TerminalHyperlink``."""
    columns: Tuple[int, int]
    destination: str


@dataclass
class HyperlinkLine:
    """Port of ``codex::terminal_hyperlinks::HyperlinkLine``."""
    line: str = ""
    hyperlinks: List[TerminalHyperlink] = field(default_factory=list)

    @classmethod
    def new(cls, line: str = "") -> "HyperlinkLine":
        return cls(line=line, hyperlinks=[])

    def width(self) -> int:
        return _display_width(self.line)

    def push_span(self, span: str, destination: Optional[str] = None) -> None:
        """Port of ``HyperlinkLine::push_span``.

        ``span`` may be either a plain ``str`` (in which case its
        display width is added to the line) or a tuple ``(style, text)``
        when the Python caller wants to attach a Style. In our ANSI
        pipeline spans are always plain strings, so the simpler path
        applies.
        """
        if isinstance(span, tuple):
            style, text = span
        else:
            text = span
        start = self.width()
        end = start + _display_width(text)
        self.line = self.line + text
        if end > start and destination is not None:
            web_dest = web_destination(destination)
            if web_dest is not None:
                self.hyperlinks.append(TerminalHyperlink(
                    columns=(start, end),
                    destination=web_dest,
                ))

    def style(self, style: Style) -> "HyperlinkLine":
        """Port of ``HyperlinkLine::style`` builder.

        The Python analogue mutates the line text to wrap it in the
        style's ANSI codes. The original Rust method returns ``self``
        with the style applied to the ratatui line; we apply the style
        to the ANSI string instead.
        """
        if style.fg is not None or style.bold or style.italic or style.underline:
            codes: List[str] = []
            if style.fg is not None:
                r, g, b = style.fg
                codes.append(f"38;2;{r};{g};{b}")
            if style.bold:
                codes.append("1")
            if style.italic:
                codes.append("3")
            if style.underline:
                codes.append("4")
            if codes:
                self.line = f"\033[{';'.join(codes)}m{self.line}\033[0m"
        return self


def _display_width(text: str) -> int:
    """Display width of a string with ANSI codes stripped."""
    return len(_ANSI_RE.sub("", text))


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


# -- Line / String conversions (port of From impls) ----------------------------

def from_line(line: str) -> HyperlinkLine:
    """Port of ``From<Line<'static>> for HyperlinkLine``."""
    return HyperlinkLine.new(line)


def from_str(text: str) -> HyperlinkLine:
    """Port of ``From<&'static str> for HyperlinkLine``."""
    return HyperlinkLine.new(text)


def from_string(text: str) -> HyperlinkLine:
    """Port of ``From<String> for HyperlinkLine``."""
    return HyperlinkLine.new(text)


def visible_lines(lines: List[HyperlinkLine]) -> List[str]:
    """Port of ``codex::terminal_hyperlinks::visible_lines``.

    Returns plain string lines — the visible projection of
    ``HyperlinkLine``s.
    """
    return [line.line for line in lines]


def plain_hyperlink_lines(lines: List[str]) -> List[HyperlinkLine]:
    """Port of ``codex::terminal_hyperlinks::plain_hyperlink_lines``."""
    return [HyperlinkLine.new(line) for line in lines]


def prefix_hyperlink_lines(
    lines: List[HyperlinkLine],
    initial_prefix: str,
    subsequent_prefix: str,
) -> List[HyperlinkLine]:
    """Port of ``codex::terminal_hyperlinks::prefix_hyperlink_lines``.

    Prepends a prefix span to each line and shifts the hyperlink column
    ranges by the prefix display width.
    """
    out: List[HyperlinkLine] = []
    for idx, mut_line in enumerate(lines):
        prefix = initial_prefix if idx == 0 else subsequent_prefix
        shift = _display_width(prefix)
        mut_line.line = prefix + mut_line.line
        mut_line.hyperlinks = [
            TerminalHyperlink(
                columns=(h.columns[0] + shift, h.columns[1] + shift),
                destination=h.destination,
            )
            for h in mut_line.hyperlinks
        ]
        out.append(mut_line)
    return out


def adaptive_wrap_hyperlink_lines(
    lines: List[HyperlinkLine],
    options: RtOptions,
) -> List[HyperlinkLine]:
    """Port of ``codex::terminal_hyperlinks::adaptive_wrap_hyperlink_lines``."""
    out: List[HyperlinkLine] = []
    for idx, line in enumerate(lines):
        opts = options if idx == 0 else options.with_initial_indent(options.subsequent_indent)
        wrapped = adaptive_wrap_line(line.line, opts)
        wrapped_static = [_line_to_static(w) for w in wrapped]
        out.extend(remap_wrapped_line(line, wrapped_static))
    return out


def annotate_web_urls(lines: List[str]) -> List[HyperlinkLine]:
    """Port of ``codex::terminal_hyperlinks::annotate_web_urls``."""
    return [annotate_web_urls_in_line(line) for line in lines]


def annotate_web_urls_in_line(line: str) -> HyperlinkLine:
    """Port of ``codex::terminal_hyperlinks::annotate_web_urls_in_line``."""
    out = HyperlinkLine.new(line)
    out.hyperlinks = web_links_in_text(line)
    return out


def remap_wrapped_line(
    source: HyperlinkLine,
    wrapped: List[str],
) -> List[HyperlinkLine]:
    """Port of ``codex::terminal_hyperlinks::remap_wrapped_line``.

    Re-attach source hyperlink ranges after visible-text wrapping has
    split a line. Link text is matched in display order so a URL split
    across lines retains the complete destination on every rendered
    fragment.
    """
    out = plain_hyperlink_lines(wrapped)
    source_text = source.line
    source_byte = 0
    source_column = 0
    for idx, line in enumerate(out):
        if idx > 0:
            trimmed = source_text[source_byte:].lstrip()
            skipped = len(source_text[source_byte:]) - len(trimmed)
            source_column += _display_width(source_text[source_byte:source_byte + skipped])
            source_byte += skipped

        rendered = line.line
        remaining = source_text[source_byte:]
        match_idx = _longest_suffix_matching_prefix(rendered, remaining)
        if match_idx is None:
            continue
        mapped = rendered[match_idx:]
        output_column = _display_width(rendered[:match_idx])
        for ch in mapped:
            width = _char_width(ch)
            link = next(
                (link for link in source.hyperlinks
                 if link.columns[0] <= source_column < link.columns[1]),
                None,
            )
            if link is not None:
                _push_link_range(line, (output_column, output_column + width), link.destination)
            source_column += width
            output_column += width
        source_byte += len(mapped)
    return out


def _longest_suffix_matching_prefix(rendered: str, source: str) -> Optional[int]:
    """Port of ``codex::terminal_hyperlinks::longest_suffix_matching_prefix``.

    Find the largest ``index`` such that ``rendered[index:]`` is a prefix
    of ``source``.
    """
    if not source.startswith(rendered):
        return None
    for idx in range(len(rendered) - 1, -1, -1):
        if source.startswith(rendered[idx:]):
            return idx
    return None


def _push_link_range(
    line: HyperlinkLine,
    range_: Tuple[int, int],
    destination: str,
) -> None:
    """Port of ``codex::terminal_hyperlinks::push_link_range``."""
    if range_[0] == range_[1]:
        return
    if line.hyperlinks:
        prev = line.hyperlinks[-1]
        if prev.destination == destination and prev.columns[1] == range_[0]:
            prev.columns = (prev.columns[0], range_[1])
            return
    line.hyperlinks.append(TerminalHyperlink(columns=range_, destination=destination))


# -- URL detection (port of web_links_in_text + helpers) ----------------------

def web_links_in_text(text: str) -> List[TerminalHyperlink]:
    """Port of ``codex::terminal_hyperlinks::web_links_in_text``."""
    links: List[TerminalHyperlink] = []
    search_from = 0
    for raw_token in text.split():
        rel = text[search_from:].find(raw_token)
        if rel < 0:
            continue
        raw_start = search_from + rel
        search_from = raw_start + len(raw_token)
        trimmed_start = 0
        for idx, ch in enumerate(raw_token):
            if not _is_leading_punctuation(ch):
                trimmed_start = idx
                break
        else:
            continue
        trimmed_end = _trailing_url_end(raw_token[trimmed_start:]) + trimmed_start
        if trimmed_start >= trimmed_end:
            continue
        candidate = raw_token[trimmed_start:trimmed_end]
        destination = web_destination(candidate)
        if destination is None:
            continue
        start = _display_width(text[:raw_start + trimmed_start])
        end = start + _display_width(candidate)
        links.append(TerminalHyperlink(columns=(start, end), destination=destination))
    return links


def _is_leading_punctuation(ch: str) -> bool:
    return ch in "()[]{}<>,.;!'\"\u2018\u2019\u201c\u201d"


def _trailing_url_end(candidate: str) -> int:
    """Port of ``codex::terminal_hyperlinks::trailing_url_end``."""
    end = len(candidate)
    while end > 0:
        remaining = candidate[:end]
        ch = remaining[-1]
        trim = ch in ",.;!'\"\u2018\u2019\u201c\u201d" or (
            ch in ")]}>" and _has_unmatched_closing_delimiter(remaining, ch)
        )
        if not trim:
            break
        end -= len(ch)
    return end


def _has_unmatched_closing_delimiter(candidate: str, closing: str) -> bool:
    opening = {")": "(", "]": "[", "}": "{", ">": "<"}.get(closing)
    if opening is None:
        return False
    return candidate.count(closing) > candidate.count(opening)


# -- URL safety (port of web_destination / osc8_hyperlink) --------------------

def web_destination(destination: str) -> Optional[str]:
    """Port of ``codex::terminal_hyperlinks::web_destination``."""
    safe = "".join(ch for ch in destination if not ch.is_control() and ord(ch) < 0x7F)
    try:
        parsed = urlparse(safe)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    return safe


def osc8_hyperlink(destination: str, text: str) -> str:
    """Port of ``codex::terminal_hyperlinks::osc8_hyperlink``."""
    safe = web_destination(destination)
    if safe is None:
        return text
    return f"\x1b]8;;{safe}\x07{text}\x1b]8;;\x07"


def strip_osc8(text: str) -> str:
    """Port of ``codex::terminal_hyperlinks::strip_osc8``."""
    out: List[str] = []
    i = 0
    while i < len(text):
        if text[i:i + 5] == "\x1b]8;;":
            i += 5
            while i < len(text):
                if text[i] == "\x07":
                    i += 1
                    break
                if i + 1 < len(text) and text[i] == "\x1b" and text[i + 1] == "\\":
                    i += 2
                    break
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


# -- Span decoration (port of decorate_spans) ---------------------------------

def decorate_spans(line: HyperlinkLine) -> List[str]:
    """Port of ``codex::terminal_hyperlinks::decorate_spans``.

    Returns a list of ANSI-wrapped string fragments. In our Python
    port we don't return ratatui ``Span`` values — we return the OSC 8
    wrapped plain-text fragments that the terminal printer concatenates.
    """
    if not line.hyperlinks:
        return [line.line]
    out: List[str] = []
    column = 0
    link_index = 0
    active_link_index: Optional[int] = None
    active_destination: Optional[str] = None
    for ch in line.line:
        width = _char_width(ch)
        while link_index < len(line.hyperlinks) and line.hyperlinks[link_index].columns[1] <= column:
            link_index += 1
        selected_link_index = None
        if link_index < len(line.hyperlinks) and line.hyperlinks[link_index].columns[0] <= column < line.hyperlinks[link_index].columns[1]:
            selected_link_index = link_index
        if active_link_index != selected_link_index:
            if active_destination is not None:
                out.append("\x1b]8;;\x07")
            active_destination = None
            if selected_link_index is not None:
                dest = web_destination(line.hyperlinks[selected_link_index].destination)
                if dest is not None:
                    out.append(f"\x1b]8;;{dest}\x07")
                    active_destination = dest
            active_link_index = selected_link_index
        out.append(ch)
        column += width
    if active_destination is not None:
        out.append("\x1b]8;;\x07")
    return ["".join(out)]


def _char_width(ch: str) -> int:
    """Port of ``UnicodeWidthChar::width`` (default 0 for control chars)."""
    code = ord(ch)
    if code < 0x20 or code == 0x7F:
        return 0
    if code < 0x1100:
        return 1
    if 0x1100 <= code <= 0x115F or 0x2E80 <= code <= 0x303E or 0x3041 <= code <= 0x33FF:
        return 2
    if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xA000 <= code <= 0xA4CF:
        return 2
    if 0xAC00 <= code <= 0xD7A3 or 0xF900 <= code <= 0xFAFF or 0xFE30 <= code <= 0xFE4F or 0xFF00 <= code <= 0xFF60 or 0xFFE0 <= code <= 0xFFE6:
        return 2
    if 0x20000 <= code <= 0x2FFFD or 0x30000 <= code <= 0x3FFFD:
        return 2
    return 1


# -- Buffer decoration (port of mark_buffer_hyperlinks / mark_matching_cells) -

def mark_buffer_hyperlinks(
    lines: List[HyperlinkLine],
    width: int,
    scroll_rows: int = 0,
) -> List[str]:
    """Port of ``codex::terminal_hyperlinks::mark_buffer_hyperlinks``.

    In our ANSI-string world we don't mutate a ratatui Buffer — we
    return the wrapped, OSC-8-decorated string per line, ready for
    terminal output.
    """
    out: List[str] = []
    logical_row = 0
    for line in lines:
        wrapped = _simple_wrap(line.line, width)
        rendered_height = max(1, len(wrapped))
        if not line.hyperlinks:
            out.extend(wrapped)
            logical_row += rendered_height
            continue
        for piece in wrapped:
            new = HyperlinkLine.new(piece)
            for link in line.hyperlinks:
                shift = 0
                if link.columns[0] >= width:
                    shift = -((link.columns[0] // width) * width)
                col0 = max(0, link.columns[0] - (logical_row * width))
                col1 = max(0, link.columns[1] - (logical_row * width))
                if col0 == col1 or col1 <= 0 or col0 >= width:
                    continue
                col0 = max(0, col0)
                col1 = min(width, col1)
                new.hyperlinks.append(TerminalHyperlink(columns=(col0, col1), destination=link.destination))
            decorated = decorate_spans(new)
            out.extend(decorated)
            logical_row += 1
    return out


def mark_url_hyperlink(text: str, destination: str) -> str:
    """Port of ``codex::terminal_hyperlinks::mark_url_hyperlink``."""
    if web_destination(destination) is None:
        return text
    return osc8_hyperlink(destination, text)


def mark_underlined_hyperlink(text: str, destination: str) -> str:
    """Port of ``codex::terminal_hyperlinks::mark_underlined_hyperlink``."""
    if web_destination(destination) is None:
        return text
    return osc8_hyperlink(destination, text)


def _simple_wrap(text: str, width: int) -> List[str]:
    if width <= 0:
        return [text]
    out: List[str] = []
    for i in range(0, len(text), width):
        out.append(text[i:i + width])
    return out


__all__ = [
    "TerminalHyperlink",
    "HyperlinkLine",
    "from_line",
    "from_str",
    "from_string",
    "visible_lines",
    "plain_hyperlink_lines",
    "prefix_hyperlink_lines",
    "adaptive_wrap_hyperlink_lines",
    "annotate_web_urls",
    "annotate_web_urls_in_line",
    "remap_wrapped_line",
    "web_links_in_text",
    "web_destination",
    "osc8_hyperlink",
    "strip_osc8",
    "decorate_spans",
    "mark_buffer_hyperlinks",
    "mark_url_hyperlink",
    "mark_underlined_hyperlink",
]
