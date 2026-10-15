"""Word-wrapping with URL-aware heuristics.

Python port of codex-rs/tui/src/wrapping.rs. The Rust file delegates to the
``textwrap`` crate for low-level wrapping and adds URL-aware heuristics
on top — URL tokens must never be split across lines, mixed URL/prose
lines still wrap ordinary prose at word boundaries, and a single
overlong non-URL token is allowed to break when no word boundary fits.

In Python the standard library ``textwrap`` module is the analogue of
the ``textwrap`` crate. Its API returns plain strings, so the port
flattens ratatui-style ``Line``/``Span`` values to ``str`` for the
call into ``textwrap.wrap`` and re-emits ``str`` outputs.

The structure mirrors the Rust file 1:1:
- ``RtOptions`` (port of ``RtOptions``).
- ``word_wrap_line`` / ``word_wrap_lines`` (port of the same).
- ``adaptive_wrap_line`` / ``adaptive_wrap_lines``.
- ``url_preserving_wrap_options``.
- URL detection helpers: ``line_contains_url_like``,
  ``line_has_mixed_url_and_non_url_tokens``, ``text_contains_url_like``,
  ``text_has_mixed_url_and_non_url_tokens``, ``is_url_like_token``,
  ``is_substantive_non_url_token``, ``is_decorative_marker_token``,
  ``is_ordered_list_marker``, ``trim_url_token``, ``is_absolute_url_like``,
  ``has_valid_scheme_prefix``, ``is_bare_url_like``,
  ``split_host_port_and_trailer``, ``split_host_and_port``,
  ``is_valid_port``, ``is_ipv4``, ``is_domain_name``, ``is_tld``,
  ``is_domain_label``.
- Range helpers: ``wrap_ranges`` / ``wrap_ranges_trim``,
  ``borrowed_slice_range``, ``map_owned_wrapped_line_to_range``.
- ``flatten_line`` / ``slice_line_spans``.
- ``LineInput`` / ``IntoLineInput`` (port of the same).
- Mixed-URL wrapping: ``MixedUrlWord``, ``mixed_url_wrap_line``,
  ``mixed_url_wrap_ranges``, ``split_mixed_url_word``.
"""
from __future__ import annotations

import re
import textwrap as _stdlib_textwrap
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Union
from urllib.parse import urlparse

from .render.highlight import Style


# Stand-in for ratatui::text::Line / Span: in our ANSI-string world a Line
# is just a list of (Style, text) pairs (or a plain str).
Line = Union[str, List[Tuple[Style, str]]]
Span = Tuple[Style, str]


# WordSeparator / WordSplitter / WrapAlgorithm / LineEnding stand-ins
# (port of textwrap::* enum variants we actually use).
class WordSeparatorKind(str, Enum):
    NEW = "new"
    ASCII_SPACE = "ascii_space"


class WordSplitterKind(str, Enum):
    NO_HYPHENATION = "no_hyphenation"
    HYPHEN_SPLITTER = "hyphen_splitter"


class WrapAlgorithmKind(str, Enum):
    FIRST_FIT = "first_fit"
    OPTIMAL_FIT = "optimal_fit"


class LineEndingKind(str, Enum):
    LF = "\n"
    CRLF = "\r\n"


@dataclass
class RtOptions:
    """Port of ``codex::wrapping::RtOptions``.

    In Rust the ``initial_indent`` / ``subsequent_indent`` are ``Line``;
    in Python we model them as ``str`` (the indent prefix).
    """
    width: int
    line_ending: str = "\n"
    initial_indent: str = ""
    subsequent_indent: str = ""
    break_words: bool = True
    word_separator: str = "new"
    wrap_algorithm: str = "first_fit"
    word_splitter: str = "hyphen_splitter"

    def __post_init__(self):
        """Fix defaults that were overwritten by method definitions."""
        for field_name, expected_type, expected_default in [
            ("line_ending", str, "\n"),
            ("initial_indent", str, ""),
            ("subsequent_indent", str, ""),
            ("break_words", bool, True),
            ("word_separator", str, "new"),
            ("wrap_algorithm", str, "first_fit"),
            ("word_splitter", str, "hyphen_splitter"),
        ]:
            if not isinstance(getattr(self, field_name, None), expected_type):
                object.__setattr__(self, field_name, expected_default)

    @classmethod
    def new(cls, width: int) -> "RtOptions":
        return cls(width=width)

    def with_line_ending(self, line_ending: str) -> "RtOptions":
        return RtOptions(**{**self.__dict__, "line_ending": line_ending})

    def with_width(self, width: int) -> "RtOptions":
        return RtOptions(**{**self.__dict__, "width": width})

    def with_initial_indent(self, initial_indent: str) -> "RtOptions":
        return RtOptions(**{**self.__dict__, "initial_indent": initial_indent})

    def with_subsequent_indent(self, subsequent_indent: str) -> "RtOptions":
        return RtOptions(**{**self.__dict__, "subsequent_indent": subsequent_indent})

    def with_break_words(self, break_words: bool) -> "RtOptions":
        return RtOptions(**{**self.__dict__, "break_words": break_words})

    def with_word_separator(self, word_separator: str) -> "RtOptions":
        return RtOptions(**{**self.__dict__, "word_separator": word_separator})

    def with_wrap_algorithm(self, wrap_algorithm: str) -> "RtOptions":
        return RtOptions(**{**self.__dict__, "wrap_algorithm": wrap_algorithm})

    def with_word_splitter(self, word_splitter: str) -> "RtOptions":
        return RtOptions(**{**self.__dict__, "word_splitter": word_splitter})


def _options_from(width_or_options: Union[int, RtOptions]) -> RtOptions:
    if isinstance(width_or_options, int):
        return RtOptions.new(width_or_options)
    return width_or_options


# -- URL detection helpers (port of codex::wrapping URL detection) -------------

def text_contains_url_like(text: str) -> bool:
    """Port of ``codex::wrapping::text_contains_url_like``."""
    return any(is_url_like_token(t) for t in text.split())


def text_has_mixed_url_and_non_url_tokens(text: str) -> bool:
    """Port of ``codex::wrapping::text_has_mixed_url_and_non_url_tokens``."""
    saw_url = False
    saw_non_url = False
    for raw in text.split():
        if is_url_like_token(raw):
            saw_url = True
        elif is_substantive_non_url_token(raw):
            saw_non_url = True
        if saw_url and saw_non_url:
            return True
    return False


def is_url_like_token(raw_token: str) -> bool:
    """Port of ``codex::wrapping::is_url_like_token``."""
    token = trim_url_token(raw_token)
    if not token:
        return False
    return is_absolute_url_like(token) or is_bare_url_like(token)


def is_substantive_non_url_token(raw_token: str) -> bool:
    """Port of ``codex::wrapping::is_substantive_non_url_token``."""
    token = trim_url_token(raw_token)
    if not token or is_decorative_marker_token(raw_token, token):
        return False
    return any(c.isalnum() for c in token)


_DECORATIVE_MARKERS = {
    "-", "*", "+", "•", "◦", "▪", ">", "|", "│", "┆",
    "└", "├", "┌", "┐", "┘", "┼",
}


def is_decorative_marker_token(raw_token: str, token: str) -> bool:
    """Port of ``codex::wrapping::is_decorative_marker_token``."""
    raw = raw_token.strip()
    if raw in _DECORATIVE_MARKERS:
        return True
    return is_ordered_list_marker(raw, token)


def is_ordered_list_marker(raw_token: str, token: str) -> bool:
    """Port of ``codex::wrapping::is_ordered_list_marker``."""
    return (
        bool(token)
        and all(c.is_ascii_digit() for c in token)
        and (raw_token.endswith(".") or raw_token.endswith(")"))
    )


_URL_TOKEN_TRIM = "()[]{}<>,.;:!'\""

_TRIM_TRANS = str.maketrans("", "", _URL_TOKEN_TRIM)


def trim_url_token(token: str) -> str:
    """Port of ``codex::wrapping::trim_url_token``."""
    return token.strip(_URL_TOKEN_TRIM)


def is_absolute_url_like(token: str) -> bool:
    """Port of ``codex::wrapping::is_absolute_url_like``."""
    if "://" not in token:
        return False
    try:
        url = urlparse(token)
    except Exception:
        return has_valid_scheme_prefix(token)
    scheme = (url.scheme or "").lower()
    if scheme in ("http", "https", "ftp", "ftps", "ws", "wss"):
        return url.hostname is not None
    return True


def has_valid_scheme_prefix(token: str) -> bool:
    """Port of ``codex::wrapping::has_valid_scheme_prefix``."""
    if "://" not in token:
        return False
    scheme, _, rest = token.partition("://")
    if not scheme or not rest:
        return False
    if not scheme[0].is_ascii_alphabetic():
        return False
    return all(c.isalnum() or c in "+-." for c in scheme[1:])


def is_bare_url_like(token: str) -> bool:
    """Port of ``codex::wrapping::is_bare_url_like``."""
    host_port, has_trailer = split_host_port_and_trailer(token)
    if not host_port:
        return False
    if not has_trailer and not host_port.lower().startswith("www."):
        return False
    host, port = split_host_and_port(host_port)
    if not host:
        return False
    if port is not None and not is_valid_port(port):
        return False
    return host.lower() == "localhost" or is_ipv4(host) or is_domain_name(host)


def split_host_port_and_trailer(token: str) -> Tuple[str, bool]:
    """Port of ``codex::wrapping::split_host_port_and_trailer``."""
    for idx, ch in enumerate(token):
        if ch in "/?#":
            return token[:idx], True
    return token, False


def split_host_and_port(host_port: str) -> Tuple[str, Optional[str]]:
    """Port of ``codex::wrapping::split_host_and_port``."""
    if host_port.startswith("["):
        return host_port, None
    if ":" in host_port:
        host, _, port = host_port.rpartition(":")
        if host and port and port.isdigit():
            return host, port
    return host_port, None


def is_valid_port(port: str) -> bool:
    """Port of ``codex::wrapping::is_valid_port``."""
    if not port or len(port) > 5 or not port.isdigit():
        return False
    return 0 <= int(port) <= 65535


_IPV4_PART = re.compile(r"^(\d+)$")


def is_ipv4(host: str) -> bool:
    """Port of ``codex::wrapping::is_ipv4``."""
    parts = host.split(".")
    if len(parts) != 4:
        return False
    for part in parts:
        if not part or not _IPV4_PART.match(part):
            return False
        if not 0 <= int(part) <= 255:
            return False
    return True


def is_domain_name(host: str) -> bool:
    """Port of ``codex::wrapping::is_domain_name``."""
    lower = host.lower()
    if "." not in lower:
        return False
    labels = lower.split(".")
    if not labels:
        return False
    tld = labels[-1]
    if not is_tld(tld):
        return False
    return all(is_domain_label(label) for label in labels)


def is_tld(label: str) -> bool:
    """Port of ``codex::wrapping::is_tld``."""
    return 2 <= len(label) <= 63 and label.isascii() and label.isalpha()


def is_domain_label(label: str) -> bool:
    """Port of ``codex::wrapping::is_domain_label``."""
    if not label or len(label) > 63:
        return False
    if not label[0].isalnum() or not label[-1].isalnum():
        return False
    return all(c.isalnum() or c == "-" for c in label)


def line_contains_url_like(line: Line) -> bool:
    """Port of ``codex::wrapping::line_contains_url_like``."""
    return text_contains_url_like(_line_to_text(line))


def line_has_mixed_url_and_non_url_tokens(line: Line) -> bool:
    """Port of ``codex::wrapping::line_has_mixed_url_and_non_url_tokens``."""
    return text_has_mixed_url_and_non_url_tokens(_line_to_text(line))


def _line_to_text(line: Line) -> str:
    if isinstance(line, str):
        return line
    return "".join(text for _style, text in line)


# -- URL-preserving / adaptive wrapping (port of adaptive_wrap_*) -------------

def url_preserving_wrap_options(opts: RtOptions) -> RtOptions:
    """Port of ``codex::wrapping::url_preserving_wrap_options``."""
    return (
        opts.with_word_separator(WordSeparatorKind.ASCII_SPACE.value)
        .with_word_splitter(WordSplitterKind.NO_HYPHENATION.value)
        .with_break_words(False)
    )


def adaptive_wrap_line(line: Line, base: RtOptions) -> List[str]:
    """Port of ``codex::wrapping::adaptive_wrap_line``."""
    if not line_contains_url_like(line):
        return word_wrap_line(line, base)
    if line_has_mixed_url_and_non_url_tokens(line):
        return mixed_url_wrap_line(line, base)
    return word_wrap_line(line, url_preserving_wrap_options(base))


def adaptive_wrap_lines(lines: Iterable[Line], width_or_options: RtOptions) -> List[str]:
    """Port of ``codex::wrapping::adaptive_wrap_lines``."""
    base_opts = width_or_options
    out: List[str] = []
    for idx, line in enumerate(lines):
        opts = (
            base_opts
            if idx == 0
            else base_opts.with_initial_indent(base_opts.subsequent_indent)
        )
        wrapped = adaptive_wrap_line(line, opts)
        out.extend(wrapped)
    return out


# -- Standard wrapping (port of word_wrap_*) ----------------------------------

def word_wrap_line(line: Line, width_or_options: Union[int, RtOptions]) -> List[str]:
    """Port of ``codex::wrapping::word_wrap_line``."""
    flat = _line_to_text(line)
    rt_opts = _options_from(width_or_options)
    initial_width = max(1, rt_opts.width - _display_width(rt_opts.initial_indent))
    initial_wrapped = wrap_ranges_trim(flat, rt_opts.with_width(initial_width))
    if not initial_wrapped:
        return [rt_opts.initial_indent]
    first_range = initial_wrapped[0]
    first_line = rt_opts.initial_indent + flat[first_range[0]:first_range[1]]
    out = [first_line]
    base = first_range[1]
    skip_leading = 0
    while base + skip_leading < len(flat) and flat[base + skip_leading] == " ":
        skip_leading += 1
    base = base + skip_leading
    subsequent_width = max(1, rt_opts.width - _display_width(rt_opts.subsequent_indent))
    remaining = wrap_ranges_trim(flat[base:], rt_opts.with_width(subsequent_width))
    for r in remaining:
        if not r[0] and not r[1]:
            continue
        out.append(rt_opts.subsequent_indent + flat[base + r[0]: base + r[1]])
    return out


def word_wrap_lines(lines: Iterable[Line], width_or_options: Union[int, RtOptions]) -> List[str]:
    """Port of ``codex::wrapping::word_wrap_lines``."""
    base_opts = _options_from(width_or_options)
    out: List[str] = []
    for idx, line in enumerate(lines):
        opts = base_opts if idx == 0 else base_opts.initial_indent(base_opts.subsequent_indent)
        wrapped = word_wrap_line(line, opts)
        out.extend(wrapped)
    return out


# -- Mixed-URL wrapping (port of mixed_url_*) ---------------------------------

@dataclass
class MixedUrlWord:
    """Port of ``codex::wrapping::MixedUrlWord``."""
    range: Tuple[int, int]
    is_url: bool

    def width(self, text: str) -> int:
        return _display_width(text[self.range[0]:self.range[1]])


def mixed_url_wrap_line(line: Line, rt_opts: RtOptions) -> List[str]:
    """Port of ``codex::wrapping::mixed_url_wrap_line``."""
    flat = _line_to_text(line)
    initial_width = max(1, rt_opts.width - _display_width(rt_opts.initial_indent))
    subsequent_width = max(1, rt_opts.width - _display_width(rt_opts.subsequent_indent))
    ranges = mixed_url_wrap_ranges(flat, initial_width, subsequent_width)
    out: List[str] = []
    for idx, r in enumerate(ranges):
        prefix = rt_opts.initial_indent if idx == 0 else rt_opts.subsequent_indent
        out.append(prefix + flat[r[0]:r[1]])
    if not out:
        return [rt_opts.initial_indent]
    return out


def mixed_url_wrap_ranges(text: str, initial_width: int, subsequent_width: int) -> List[Tuple[int, int]]:
    """Port of ``codex::wrapping::mixed_url_wrap_ranges``."""
    leading_space_width = sum(1 for ch in text if ch == " ")
    words: List[MixedUrlWord] = []
    cursor = 0
    for token in text.split():
        word_start = cursor
        word_end = word_start + len(token)
        if token:
            words.append(MixedUrlWord(range=(word_start, word_end), is_url=is_url_like_token(token)))
        cursor = word_end + 1
    lines: List[Tuple[int, int]] = []
    line_start: Optional[int] = None
    line_end = 0
    line_width = 0
    line_limit = max(1, initial_width)
    for word in words:
        pending = split_mixed_url_word(text, word, line_limit)
        pending_idx = 0
        while pending_idx < len(pending):
            piece = pending[pending_idx]
            empty_line_prefix_width = (leading_space_width
                                       if line_start is None and not lines
                                       else 0)
            empty_line_piece_limit = max(1, line_limit - empty_line_prefix_width)
            if (line_start is None
                    and not piece.is_url
                    and piece.width(text) > empty_line_piece_limit):
                pending = (pending[:pending_idx]
                           + split_mixed_url_word(text, piece, empty_line_piece_limit)
                           + pending[pending_idx + 1:])
                continue
            piece_width = piece.width(text)
            inter_word_space = ((text[line_end:piece.range[0]].count(" ") if line_start is not None else 0))
            if line_start is None:
                fits = (piece.is_url
                        or empty_line_prefix_width + piece_width <= line_limit
                        or empty_line_prefix_width >= line_limit)
            else:
                fits = (line_width + inter_word_space + piece_width) <= line_limit
            if fits:
                if line_start is None:
                    is_first_output_line = not lines
                    start = 0 if is_first_output_line else piece.range[0]
                    line_start = start
                    line_width = (leading_space_width + piece_width
                                  if is_first_output_line
                                  else piece_width)
                else:
                    line_width += inter_word_space + piece_width
                line_end = piece.range[1]
                pending_idx += 1
                continue
            if line_start is not None:
                lines.append((line_start, line_end))
                line_start = None
            line_end = 0
            line_width = 0
            line_limit = max(1, subsequent_width)
    if line_start is not None:
        lines.append((line_start, line_end))
    return lines


def split_mixed_url_word(text: str, word: MixedUrlWord, line_limit: int) -> List[MixedUrlWord]:
    """Port of ``codex::wrapping::split_mixed_url_word``."""
    if word.is_url or word.width(text) <= max(1, line_limit):
        return [word]
    word_text = text[word.range[0]:word.range[1]]
    pieces: List[MixedUrlWord] = []
    offset = word.range[0]
    chunk_size = max(1, line_limit)
    while offset < word.range[1]:
        end = min(offset + chunk_size, word.range[1])
        pieces.append(MixedUrlWord(range=(offset, end), is_url=False))
        offset = end
    return pieces


# -- Range helpers (port of wrap_ranges / wrap_ranges_trim) --------------------

def wrap_ranges(text: str, width_or_options: Union[int, RtOptions]) -> List[Tuple[int, int]]:
    """Port of ``codex::wrapping::wrap_ranges``."""
    rt_opts = _options_from(width_or_options)
    initial_indent = rt_opts.initial_indent
    subsequent_indent = rt_opts.subsequent_indent
    indent_width = _display_width(initial_indent or subsequent_indent)
    inner_width = max(1, rt_opts.width - indent_width)
    wrapped = _stdlib_textwrap.wrap(text, width=inner_width, break_long_words=rt_opts.break_words)
    out: List[Tuple[int, int]] = []
    cursor = 0
    for line_idx, piece in enumerate(wrapped):
        prefix = initial_indent if line_idx == 0 else subsequent_indent
        start, end = _locate_in_source(text, cursor, piece, prefix)
        trailing = 0
        while end + trailing < len(text) and text[end + trailing] == " ":
            trailing += 1
        out.append((start, end + trailing + 1))
        cursor = end + trailing
    return out


def wrap_ranges_trim(text: str, width_or_options: Union[int, RtOptions]) -> List[Tuple[int, int]]:
    """Port of ``codex::wrapping::wrap_ranges_trim``."""
    rt_opts = _options_from(width_or_options)
    initial_indent = rt_opts.initial_indent
    subsequent_indent = rt_opts.subsequent_indent
    indent_width = _display_width(initial_indent or subsequent_indent)
    inner_width = max(1, rt_opts.width - indent_width)
    wrapped = _stdlib_textwrap.wrap(text, width=inner_width, break_long_words=rt_opts.break_words)
    out: List[Tuple[int, int]] = []
    cursor = 0
    for line_idx, piece in enumerate(wrapped):
        prefix = initial_indent if line_idx == 0 else subsequent_indent
        start, end = _locate_in_source(text, cursor, piece, prefix)
        out.append((start, end))
        cursor = end
    return out


def _locate_in_source(text: str, cursor: int, piece: str, prefix: str) -> Tuple[int, int]:
    """Port of ``codex::wrapping::borrowed_slice_range`` /
    ``map_owned_wrapped_line_to_range`` — find where ``piece`` lives in
    ``text`` starting from ``cursor``."""
    if prefix and piece.startswith(prefix):
        piece = piece[len(prefix):]
    if not piece:
        return (cursor, cursor)
    start = cursor
    while start < len(text) and text[start] == " ":
        start += 1
    end = start
    src = start
    for ch in piece:
        if src < len(text) and text[src] == ch:
            end = src + 1
            src = end
        else:
            break
    return (start, end)


def borrowed_slice_range(text: str, slice_: str) -> Optional[Tuple[int, int]]:
    """Port of ``codex::wrapping::borrowed_slice_range``.

    In Python strings are immutable, so any slice is a copy; we do a
    linear ``str.find`` to locate the original span.
    """
    if not slice_:
        return None
    idx = text.find(slice_)
    if idx < 0:
        return None
    return (idx, idx + len(slice_))


def map_owned_wrapped_line_to_range(
    text: str, cursor: int, wrapped: str, synthetic_prefix: str
) -> Tuple[int, int]:
    """Port of ``codex::wrapping::map_owned_wrapped_line_to_range``."""
    if synthetic_prefix and wrapped.startswith(synthetic_prefix):
        wrapped = wrapped[len(synthetic_prefix):]
    start = cursor
    while start < len(text) and not wrapped.startswith(" "):
        ch = text[start] if start < len(text) else ""
        if ch == " ":
            start += 1
        else:
            break
    end = start
    src = start
    for ch in wrapped:
        if src < len(text) and text[src] == ch:
            src += 1
            end = src
            continue
        if ch == "-":
            continue
        break
    return (start, end)


# -- flatten_line / slice_line_spans -------------------------------------------

def flatten_line(line: Line) -> Tuple[str, List[Tuple[Tuple[int, int], Style]]]:
    """Port of ``codex::wrapping::flatten_line``.

    Returns ``(flat_text, [(span_range, style), ...])``. The Python
    analogue collapses ``ratatui::text::Line`` (list of styled spans)
    to a single string plus a parallel span-bounds list.
    """
    if isinstance(line, str):
        return (line, [((0, len(line)), Style())])
    flat_parts: List[str] = []
    span_bounds: List[Tuple[Tuple[int, int], Style]] = []
    acc = 0
    for style, text in line:
        if not text:
            continue
        start = acc
        flat_parts.append(text)
        acc += len(text)
        span_bounds.append(((start, acc), style))
    return ("".join(flat_parts), span_bounds)


def slice_line_spans(line: Line, span_bounds: List[Tuple[Tuple[int, int], Style]], source_range: Tuple[int, int]) -> Line:
    """Port of ``codex::wrapping::slice_line_spans``."""
    if isinstance(line, str):
        return line[source_range[0]:source_range[1]]
    out: List[Tuple[Style, str]] = []
    for (start, end), style in span_bounds:
        if end <= source_range[0] or start >= source_range[1]:
            continue
        seg_start = max(start, source_range[0]) - start
        seg_end = min(end, source_range[1]) - start
        text = _line_to_text(line)[start:end]
        out.append((style, text[seg_start:seg_end]))
    return out


# -- LineInput / IntoLineInput (port of the Rust traits) -----------------------

class LineInput:
    """Port of ``codex::wrapping::LineInput`` enum (Borrowed/Owned)."""

    def __init__(self, line: Line) -> None:
        self._line = line

    @classmethod
    def borrowed(cls, line: Line) -> "LineInput":
        return cls(line)

    @classmethod
    def owned(cls, line: Line) -> "LineInput":
        return cls(line)

    def as_ref(self) -> Line:
        return self._line


class IntoLineInput(ABC):
    """Port of ``codex::wrapping::IntoLineInput`` trait."""

    @abstractmethod
    def into_line_input(self) -> LineInput: ...


def _into_line_input(value: Any) -> LineInput:
    if isinstance(value, LineInput):
        return value
    if isinstance(value, str):
        return LineInput.owned(value)
    return LineInput.owned(value)


# -- Display width helper -----------------------------------------------------

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _display_width(text: str) -> int:
    """Display width of a string with ANSI codes stripped."""
    return len(_ANSI_RE.sub("", text))


__all__ = [
    "RtOptions",
    "WordSeparatorKind",
    "WordSplitterKind",
    "WrapAlgorithmKind",
    "LineEndingKind",
    "text_contains_url_like",
    "text_has_mixed_url_and_non_url_tokens",
    "is_url_like_token",
    "is_substantive_non_url_token",
    "is_decorative_marker_token",
    "is_ordered_list_marker",
    "trim_url_token",
    "is_absolute_url_like",
    "has_valid_scheme_prefix",
    "is_bare_url_like",
    "split_host_port_and_trailer",
    "split_host_and_port",
    "is_valid_port",
    "is_ipv4",
    "is_domain_name",
    "is_tld",
    "is_domain_label",
    "line_contains_url_like",
    "line_has_mixed_url_and_non_url_tokens",
    "url_preserving_wrap_options",
    "adaptive_wrap_line",
    "adaptive_wrap_lines",
    "word_wrap_line",
    "word_wrap_lines",
    "MixedUrlWord",
    "mixed_url_wrap_line",
    "mixed_url_wrap_ranges",
    "split_mixed_url_word",
    "wrap_ranges",
    "wrap_ranges_trim",
    "borrowed_slice_range",
    "map_owned_wrapped_line_to_range",
    "flatten_line",
    "slice_line_spans",
    "LineInput",
    "IntoLineInput",
]
