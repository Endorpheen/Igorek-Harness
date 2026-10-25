"""Low-level markdown event renderer for the TUI transcript.

Python port of codex-rs/tui/src/markdown_render.rs. This module consumes
``markdown-it-py`` tokens (the Python analogue of ``pulldown-cmark``
events) and emits styled terminal lines, including table layout,
width-aware wrapping, and local file-link display.

The structure mirrors the Rust file 1:1. ``MarkdownEvent`` models the
pulldown-cmark event stream as a flat enum so the ``Writer`` methods
mirror the Rust ``match event`` block exactly. ``markdown_it_to_events``
flattens ``markdown-it-py`` tokens into the same shape.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from markdown_it import MarkdownIt
from markdown_it.token import Token

from ..render.highlight import Style, highlight_code_to_lines
from ..render.line_utils import line_to_static as _line_to_static
from ..terminal_hyperlinks import (
    HyperlinkLine,
    TerminalHyperlink,
    _char_width,
    annotate_web_urls_in_line,
    plain_hyperlink_lines,
    prefix_hyperlink_lines,
    remap_wrapped_line,
    web_destination,
)
from ..wrapping import RtOptions, word_wrap_line, wrap_ranges_trim


# Layout constants (port of TABLE_COLUMN_GAP / TABLE_CELL_PADDING).
TABLE_COLUMN_GAP = 1
TABLE_CELL_PADDING = 1
TABLE_HEADER_SEPARATOR_CHAR = "━"
TABLE_BODY_SEPARATOR_CHAR = "─"


# -- Style table (port of MarkdownStyles) --------------------------------------

@dataclass
class MarkdownStyles:
    """Style table for markdown elements — port of ``MarkdownStyles``."""
    h1: Style = field(default_factory=lambda: Style(bold=True, underline=True))
    h2: Style = field(default_factory=lambda: Style(bold=True))
    h3: Style = field(default_factory=lambda: Style(bold=True, italic=True))
    h4: Style = field(default_factory=lambda: Style(italic=True))
    h5: Style = field(default_factory=lambda: Style(italic=True))
    h6: Style = field(default_factory=lambda: Style(italic=True))
    code: Style = field(default_factory=lambda: Style(fg=(86, 156, 214)))
    emphasis: Style = field(default_factory=lambda: Style(italic=True))
    strong: Style = field(default_factory=lambda: Style(bold=True))
    strikethrough: Style = field(default_factory=Style)
    ordered_list_marker: Style = field(default_factory=lambda: Style(fg=(86, 156, 214)))
    unordered_list_marker: Style = field(default_factory=Style)
    link: Style = field(default_factory=lambda: Style(fg=(86, 156, 214), underline=True))
    blockquote: Style = field(default_factory=lambda: Style(fg=(0, 170, 0)))


# -- MarkdownEvent (port of pulldown-cmark::Event / Tag / TagEnd) ---------------

class MarkdownEventKind(str, Enum):
    """Port of pulldown-cmark Event kinds."""
    START = "start"
    END = "end"
    TEXT = "text"
    CODE = "code"
    SOFT_BREAK = "soft_break"
    HARD_BREAK = "hard_break"
    RULE = "rule"
    HTML = "html"
    INLINE_HTML = "inline_html"
    FOOTNOTE_REFERENCE = "footnote_reference"
    TASK_LIST_MARKER = "task_list_marker"


class MarkdownTag(str, Enum):
    """Port of pulldown-cmark Tag kinds — flat enum over Tag+TagEnd."""
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    BLOCK_QUOTE = "blockquote"
    CODE_BLOCK = "code_block"
    LIST = "list"
    ITEM = "item"
    EMPHASIS = "emphasis"
    STRONG = "strong"
    STRIKETHROUGH = "strikethrough"
    LINK = "link"
    TABLE = "table"
    TABLE_HEAD = "thead"
    TABLE_ROW = "tr"
    TABLE_CELL = "td"
    HTML_BLOCK = "html_block"
    FOOTNOTE_DEFINITION = "footnote_definition"
    IMAGE = "image"
    METADATA_BLOCK = "metadata_block"


class HeadingLevel(Enum):
    """Port of pulldown-cmark HeadingLevel."""
    H1 = 1
    H2 = 2
    H3 = 3
    H4 = 4
    H5 = 5
    H6 = 6


class CodeBlockKind:
    """Port of pulldown-cmark CodeBlockKind variants."""
    Fenced = "fenced"
    INDENTED = "indented"


@dataclass
class MarkdownEvent:
    """Port of pulldown-cmark::Event. ``payload`` carries the variant
    data (heading level, link dest, codeblock lang, etc.)."""
    kind: MarkdownEventKind
    tag: Optional[MarkdownTag] = None
    payload: Any = None
    text: str = ""
    source_range: Tuple[int, int] = (0, 0)


def markdown_it_to_events(tokens: Sequence[Token]) -> List[MarkdownEvent]:
    """Convert ``markdown-it-py`` tokens into ``MarkdownEvent``s.

    Mirrors the pulldown-cmark event stream so the ``Writer`` can be
    ported 1:1.
    """
    events: List[MarkdownEvent] = []
    pos = 0
    i = 0
    while i < len(tokens):
        token = tokens[i]
        end_pos = pos + len(token.content or "")
        source_range = (pos, end_pos)
        t = token.type
        if t == "heading_open":
            level_str = (token.tag or "h1").lstrip("h")
            try:
                level = int(level_str) if level_str else 1
            except ValueError:
                level = 1
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.HEADING,
                payload=level,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "heading_close":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.HEADING,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "paragraph_open":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.PARAGRAPH,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "paragraph_close":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.PARAGRAPH,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "blockquote_open":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.BLOCK_QUOTE,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "blockquote_close":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.BLOCK_QUOTE,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "bullet_list_open":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.LIST,
                payload=None,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "bullet_list_close":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.LIST,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "ordered_list_open":
            start = 1
            if token.attrs and "start" in token.attrs:
                try:
                    start = int(token.attrs["start"])
                except (TypeError, ValueError):
                    start = 1
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.LIST,
                payload=start,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "ordered_list_close":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.LIST,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "list_item_open":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.ITEM,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "list_item_close":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.ITEM,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "inline":
            children = token.children or []
            for child in children:
                ctype = child.type
                if ctype == "text":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.TEXT,
                        text=child.content or "",
                    ))
                elif ctype == "code_inline":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.CODE,
                        text=child.content or "",
                    ))
                elif ctype == "strong_open":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.START,
                        tag=MarkdownTag.STRONG,
                    ))
                elif ctype == "strong_close":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.END,
                        tag=MarkdownTag.STRONG,
                    ))
                elif ctype == "em_open":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.START,
                        tag=MarkdownTag.EMPHASIS,
                    ))
                elif ctype == "em_close":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.END,
                        tag=MarkdownTag.EMPHASIS,
                    ))
                elif ctype == "s_open":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.START,
                        tag=MarkdownTag.STRIKETHROUGH,
                    ))
                elif ctype == "s_close":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.END,
                        tag=MarkdownTag.STRIKETHROUGH,
                    ))
                elif ctype == "link_open":
                    dest = child.attrs.get("href", "") if child.attrs else ""
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.START,
                        tag=MarkdownTag.LINK,
                        payload=dest,
                    ))
                elif ctype == "link_close":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.END,
                        tag=MarkdownTag.LINK,
                    ))
                elif ctype == "softbreak":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.SOFT_BREAK,
                    ))
                elif ctype == "hardbreak":
                    events.append(MarkdownEvent(
                        kind=MarkdownEventKind.HARD_BREAK,
                    ))
            i += 1
            continue
        if t == "fence":
            lang = (token.info or "").split()[0] if token.info else None
            content = token.content or ""
            if content.endswith("\n"):
                content = content[:-1]
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.CODE_BLOCK,
                payload={"kind": CodeBlockKind.Fenced, "lang": lang, "indent": None},
            ))
            if content:
                events.append(MarkdownEvent(
                    kind=MarkdownEventKind.TEXT,
                    text=content,
                ))
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.CODE_BLOCK,
            ))
            i += 1
            continue
        if t == "code_block":
            content = token.content or ""
            if content.endswith("\n"):
                content = content[:-1]
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.CODE_BLOCK,
                payload={"kind": CodeBlockKind.INDENTED, "lang": None, "indent": " " * 4},
            ))
            if content:
                events.append(MarkdownEvent(
                    kind=MarkdownEventKind.TEXT,
                    text=content,
                ))
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.CODE_BLOCK,
            ))
            i += 1
            continue
        if t == "hr":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.RULE,
            ))
            i += 1
            continue
        if t == "table_open":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.TABLE,
                payload=[],
            ))
            i += 1
            continue
        if t == "table_close":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.TABLE,
            ))
            i += 1
            continue
        if t == "thead_open":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.TABLE_HEAD,
            ))
            i += 1
            continue
        if t == "thead_close":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.TABLE_HEAD,
            ))
            i += 1
            continue
        if t == "tbody_open" or t == "tbody_close":
            i += 1
            continue
        if t == "tr_open":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.TABLE_ROW,
                source_range=source_range,
            ))
            i += 1
            continue
        if t == "tr_close":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.TABLE_ROW,
            ))
            i += 1
            continue
        if t == "th_open" or t == "td_open":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.START,
                tag=MarkdownTag.TABLE_CELL,
            ))
            i += 1
            continue
        if t == "th_close" or t == "td_close":
            events.append(MarkdownEvent(
                kind=MarkdownEventKind.END,
                tag=MarkdownTag.TABLE_CELL,
            ))
            i += 1
            continue
        i += 1
    return events


# -- Indent / list state (port of IndentContext) -------------------------------

@dataclass
class IndentContext:
    """Port of ``IndentContext`` — prefix, marker, is_list."""
    prefix: List[str]
    marker: Optional[List[str]]
    is_list: bool

    @classmethod
    def new(
        cls, prefix: List[str], marker: Optional[List[str]], is_list: bool
    ) -> "IndentContext":
        return cls(prefix=prefix, marker=marker, is_list=is_list)


# -- Table state (port of TableCell / TableBodyRow / TableState) ---------------

@dataclass
class TableCell:
    """Styled content of a single cell — port of ``TableCell``."""
    lines: List[str] = field(default_factory=list)

    def ensure_line(self) -> None:
        if not self.lines:
            self.lines.append("")

    def push_span(self, span: str) -> None:
        self.ensure_line()
        if self.lines:
            self.lines[-1] = self.lines[-1] + span

    def hard_break(self) -> None:
        self.lines.append("")

    def plain_text(self) -> str:
        return " ".join(self.lines)


@dataclass
class TableBodyRow:
    """A body row in a parsed table — port of ``TableBodyRow``."""
    cells: List[TableCell]
    has_table_pipe_syntax: bool


@dataclass
class TableState:
    """Accumulates table events into a structured representation.

    Port of ``TableState``. Created on table open, consumed on table close.
    """
    alignments: List[str]
    header: Optional[List[TableCell]] = None
    rows: List[TableBodyRow] = field(default_factory=list)
    current_row: Optional[List[TableCell]] = None
    current_row_has_table_pipe_syntax: bool = False
    current_cell: Optional[TableCell] = None
    in_header: bool = False

    @classmethod
    def new(cls, alignments: List[str]) -> "TableState":
        return cls(alignments=alignments)


@dataclass
class RenderedTableLines:
    """Rendered table output split by wrapping behavior."""
    table_lines: List[str]
    table_lines_prewrapped: bool
    spillover_lines: List[str]


# -- Table column classification (port of TableColumnKind / TableColumnMetrics)

class TableColumnKind:
    """Classification of a table column for width-allocation priority."""
    Narrative = "narrative"
    TokenHeavy = "token_heavy"
    Compact = "compact"


@dataclass
class TableColumnMetrics:
    """Per-column statistics used to drive the width-allocation algorithm."""
    max_width: int
    header_token_width: int
    body_token_width: int
    kind: str


# -- Link state (port of LinkState) -------------------------------------------

@dataclass
class LinkState:
    """Port of ``LinkState``."""
    destination: str
    show_destination: bool
    local_target_display: Optional[str] = None


# -- HyperlinkLine (port of HyperlinkLine) -------------------------------------

@dataclass
class HyperlinkLine:
    """A styled line plus embedded terminal hyperlinks.

    Port of codex::terminal_hyperlinks::HyperlinkLine. In our ANSI-string
    world we collapse the styled line back to a single ``str`` with
    embedded ANSI codes; hyperlinks are tracked separately for terminal
    applications that support OSC 8 escape sequences.
    """
    line: str = ""
    hyperlinks: List[Tuple[int, int, str]] = field(default_factory=list)

    @classmethod
    def new(cls, line: str = "") -> "HyperlinkLine":
        return cls(line=line, hyperlinks=[])

    def width(self) -> int:
        return _visible_width(self.line)

    def push_span(self, span: str, _destination: Optional[str] = None) -> None:
        self.line = self.line + span


# -- Local link helpers (port of codex::markdown_render local link API) -------

_COLON_LOCATION_SUFFIX_RE = re.compile(r":\d+(?::\d+)?(?:[-–]\d+(?::\d+)?)?$")
_HASH_LOCATION_SUFFIX_RE = re.compile(r"^L\d+(?:C\d+)?(?:-L\d+(?:C\d+)?)?$")


def should_render_link_destination(dest_url: str) -> bool:
    """Whether to render the destination URL alongside the link label.

    Port of ``codex::markdown_render::should_render_link_destination``.
    Local file-like links use the destination as the displayed text
    instead of the markdown label, so we suppress the URL.
    """
    return not is_local_path_like_link(dest_url)


def is_local_path_like_link(dest_url: str) -> bool:
    """Heuristic: is ``dest_url`` a local path-like destination?

    Port of ``codex::markdown_render::is_local_path_like_link``.
    """
    if dest_url.startswith("file://"):
        return True
    if dest_url.startswith(("~/", "./", "../", "\\\\", "/")):
        return True
    b = dest_url.encode("ascii", errors="ignore")
    if len(b) >= 3 and b[0:1].isalpha() and b[1:2] == b":" and b[2:3] in (b"/", b"\\"):
        return True
    return False


def render_local_link_target(dest_url: str, cwd: Optional[Path]) -> Optional[str]:
    """Render a local-link destination into display text.

    Port of ``codex::markdown_render::render_local_link_target``.
    """
    parsed = parse_local_link_target(dest_url)
    if parsed is None:
        return None
    path_text, location_suffix = parsed
    rendered = display_local_link_path(path_text, cwd)
    if location_suffix:
        rendered = rendered + location_suffix
    return rendered


def parse_local_link_target(dest_url: str) -> Optional[Tuple[str, Optional[str]]]:
    """Split a local-link destination into ``(path_text, location_suffix)``.

    Port of ``codex::markdown_render::parse_local_link_target``.
    """
    if dest_url.startswith("file://"):
        url = urlparse(dest_url)
        path_text = file_url_to_local_path_text(url)
        if path_text is None:
            return None
        location_suffix: Optional[str] = None
        if url.fragment:
            location_suffix = normalize_hash_location_suffix_fragment(url.fragment)
        return (path_text, location_suffix)

    path_text = dest_url
    location_suffix: Optional[str] = None
    if "#" in dest_url:
        candidate_path, _, fragment = dest_url.rpartition("#")
        normalized = normalize_hash_location_suffix_fragment(fragment)
        if normalized is not None:
            path_text = candidate_path
            location_suffix = normalized
    if location_suffix is None:
        suffix = extract_colon_location_suffix(path_text)
        if suffix is not None:
            path_text = path_text[: len(path_text) - len(suffix)]
            location_suffix = suffix

    try:
        decoded = unquote(path_text, errors="strict")
    except Exception:
        decoded = path_text
    return (expand_local_link_path(decoded), location_suffix)


def normalize_hash_location_suffix_fragment(fragment: str) -> Optional[str]:
    """Normalize a hash fragment like ``L12`` into the display suffix.

    Port of ``codex::markdown_render::normalize_hash_location_suffix_fragment``.
    """
    if not _HASH_LOCATION_SUFFIX_RE.match(fragment):
        return None
    return f"#{fragment}"


def extract_colon_location_suffix(path_text: str) -> Optional[str]:
    """Extract a trailing ``:line``, ``:line:col``, or range suffix.

    Port of ``codex::markdown_render::extract_colon_location_suffix``.
    """
    m = _COLON_LOCATION_SUFFIX_RE.search(path_text)
    if m is None or m.end() != len(path_text):
        return None
    return m.group(0)


def expand_local_link_path(path_text: str) -> str:
    """Expand ``~/...`` and normalize separators for display.

    Port of ``codex::markdown_render::expand_local_link_path``.
    """
    if path_text.startswith("~/") or path_text == "~":
        home = Path.home()
        rest = path_text[1:]
        if rest.startswith("/"):
            rest = rest[1:]
        if str(home):
            return normalize_local_link_path_text(str(home / rest))
    return normalize_local_link_path_text(path_text)


def file_url_to_local_path_text(url) -> Optional[str]:
    """Convert a ``file://`` URL into normalized local-path text.

    Port of ``codex::markdown_render::file_url_to_local_path_text``.
    ``url`` is a ``urllib.parse.ParseResult``.
    """
    try:
        path_text = unquote(url.path, errors="strict")
    except UnicodeDecodeError:
        path_text = url.path
    hostname = url.hostname
    if hostname and hostname != "" and hostname != "localhost":
        path_text = f"//{hostname}{path_text}"
    elif len(path_text) >= 4 and path_text[0] == "/" and path_text[2] == ":" and path_text[1].isalpha() and path_text[3] == "/":
        path_text = path_text[1:]
    return normalize_local_link_path_text(path_text)


def normalize_local_link_path_text(path_text: str) -> str:
    """Normalize local-path text into transcript display form.

    Port of ``codex::markdown_render::normalize_local_link_path_text``.
    """
    if path_text.startswith("\\\\"):
        rest = path_text[2:].replace("\\", "/").lstrip("/")
        return f"//{rest}"
    return path_text.replace("\\", "/")


def is_absolute_local_link_path(path_text: str) -> bool:
    """Whether ``path_text`` is an absolute local link path.

    Port of ``codex::markdown_render::is_absolute_local_link_path``.
    """
    if path_text.startswith("/") or path_text.startswith("//"):
        return True
    b = path_text.encode("ascii", errors="ignore")
    return len(b) >= 3 and b[0:1].isalpha() and b[1:2] == b":" and b[2:3] == b"/"


def trim_trailing_local_path_separator(path_text: str) -> str:
    """Remove trailing separators from a local path without destroying roots.

    Port of ``codex::markdown_render::trim_trailing_local_path_separator``.
    """
    if path_text in ("/", "//"):
        return path_text
    b = path_text.encode("ascii", errors="ignore")
    if len(b) == 3 and b[0:1].isalpha() and b[1:2] == b":" and b[2:3] == b"/":
        return path_text
    return path_text.rstrip("/")


def strip_local_path_prefix(path_text: str, cwd_text: str) -> Optional[str]:
    """Strip ``cwd_text`` from the start of ``path_text`` when strictly underneath.

    Port of ``codex::markdown_render::strip_local_path_prefix``.
    """
    path_text = trim_trailing_local_path_separator(path_text)
    cwd_text = trim_trailing_local_path_separator(cwd_text)
    if path_text == cwd_text:
        return None
    if cwd_text in ("/", "//"):
        return path_text.lstrip("/")
    if path_text.startswith(cwd_text + "/"):
        rest = path_text[len(cwd_text):]
        return rest.lstrip("/")
    return None


def display_local_link_path(path_text: str, cwd: Optional[Path]) -> str:
    """Render a local link path for the transcript, shortening against ``cwd``.

    Port of ``codex::markdown_render::display_local_link_path``.
    """
    if cwd is not None and is_absolute_local_link_path(path_text):
        cwd_text = str(cwd)
        relative = strip_local_path_prefix(path_text, cwd_text)
        if relative is not None:
            return relative
    return path_text


# -- Public API ----------------------------------------------------------------

def render_markdown_text(input: str) -> List[str]:
    """Render markdown with default wrapping behavior.

    Port of ``codex::markdown_render::render_markdown_text``.
    """
    return render_markdown_text_with_width(input, None)


def render_markdown_text_with_width(input: str, width: Optional[int]) -> List[str]:
    """Render markdown constrained to a known terminal width.

    Port of ``codex::markdown_render::render_markdown_text_with_width``.
    """
    cwd: Optional[Path] = None
    try:
        cwd = Path.cwd()
    except OSError:
        cwd = None
    return render_markdown_text_with_width_and_cwd(input, width, cwd)


def render_markdown_text_with_width_and_cwd(
    input: str, width: Optional[int], cwd: Optional[Path]
) -> List[str]:
    """Render markdown with an explicit working directory for local file links.

    Port of ``codex::markdown_render::render_markdown_text_with_width_and_cwd``.
    """
    lines = render_markdown_lines_with_width_and_cwd(input, width, cwd)
    return _visible_lines(lines)


def render_markdown_lines_with_width_and_cwd(
    input: str, width: Optional[int], cwd: Optional[Path]
) -> List[HyperlinkLine]:
    """Render markdown into ``HyperlinkLine``s.

    Port of ``codex::markdown_render::render_markdown_lines_with_width_and_cwd``.
    """
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable(["table", "strikethrough"])
    tokens = md.parse(input)
    events = markdown_it_to_events(tokens)
    writer = Writer(events, width, cwd)
    writer.run()
    return writer.output_lines


# -- Writer (Python port of codex::markdown_render::Writer) ---------------------

class Writer:
    """Stateful markdown event consumer that builds styled lines.

    Port of ``codex::markdown_render::Writer<'a, I>``. The ``events``
    field is the list of ``MarkdownEvent``s produced by
    ``markdown_it_to_events`` — the Python analogue of the pulldown-cmark
    ``Parser::new_ext(...)`` event iterator.
    """

    def __init__(
        self,
        events: List[MarkdownEvent],
        wrap_width: Optional[int],
        cwd: Optional[Path],
    ) -> None:
        self.events = events
        self.event_idx = 0
        self.output_lines: List[HyperlinkLine] = []  # port of Writer.text field
        self.styles = MarkdownStyles()
        self.inline_styles: List[Style] = []
        self.indent_stack: List[IndentContext] = []
        self.list_indices: List[Optional[int]] = []
        self.list_needs_blank_before_next_item: List[bool] = []
        self.list_item_start_line_counts: List[int] = []
        self.link: Optional[LinkState] = None
        self.needs_newline = False
        self.pending_marker_line = False
        self.in_paragraph = False
        self.in_code_block = False
        self.code_block_lang: Optional[str] = None
        self.code_block_buffer: str = ""
        self.wrap_width = wrap_width
        self.cwd = cwd
        self.line_ends_with_local_link_target = False
        self.pending_local_link_soft_break = False
        self.current_line_content: Optional[HyperlinkLine] = None
        self.current_initial_indent: List[str] = []
        self.current_subsequent_indent: List[str] = []
        self.current_line_style: Style = Style()
        self.current_line_in_code_block: bool = False
        self.table_state: Optional[TableState] = None
        # Output line indices belonging to fenced code blocks: these keep
        # their verbatim whitespace and are never width-wrapped.
        self._code_line_indices: set = set()

    def run(self) -> None:
        while self.event_idx < len(self.events):
            event = self.events[self.event_idx]
            self.event_idx += 1
            self.handle_event(event)
        self.flush_current_line()
        self._wrap_output_lines()

    def _note_output_line(self) -> None:
        """Mark the most recently emitted line as code-block content."""
        if self.in_code_block and self.output_lines:
            self._code_line_indices.add(len(self.output_lines) - 1)

    def _wrap_output_lines(self) -> None:
        """Wrap plain output lines to ``wrap_width`` (port of Rust flush).

        Code-block lines are skipped so copy/paste keeps its whitespace;
        tables are already laid out to fit and are shorter than the width.
        """
        width = self.wrap_width
        if not width or width <= 0:
            return
        has_overflow = any(
            idx not in self._code_line_indices
            and _visible_width(line.line) > width
            for idx, line in enumerate(self.output_lines)
        )
        if not has_overflow:
            return
        wrapped: List[HyperlinkLine] = []
        for idx, line in enumerate(self.output_lines):
            if idx in self._code_line_indices or _visible_width(line.line) <= width:
                wrapped.append(line)
                continue
            indent = _leading_whitespace_visible(line.line)
            for piece in _ansi_safe_wrap_line_to_width(line.line, width, indent):
                wrapped.append(HyperlinkLine.new(piece))
        self.output_lines = wrapped

    def handle_event(self, event: MarkdownEvent) -> None:
        self.prepare_for_event(event)
        if event.kind == MarkdownEventKind.START and event.tag is not None:
            self.start_tag(event)
        elif event.kind == MarkdownEventKind.END and event.tag is not None:
            self.end_tag(event)
        elif event.kind == MarkdownEventKind.TEXT:
            self.text(event.text)  # method call: self.text = Writer::text port
        elif event.kind == MarkdownEventKind.CODE:
            self.code(event.text)
        elif event.kind == MarkdownEventKind.SOFT_BREAK:
            self.soft_break()
        elif event.kind == MarkdownEventKind.HARD_BREAK:
            self.hard_break()
        elif event.kind == MarkdownEventKind.RULE:
            self.flush_current_line()
            if self.output_lines:
                self.push_blank_line()
            self.push_line(HyperlinkLine.new("———"))
            self.needs_newline = True
        elif event.kind == MarkdownEventKind.HTML:
            self.html(event.text, inline=False)
        elif event.kind == MarkdownEventKind.INLINE_HTML:
            self.html(event.text, inline=True)
        elif event.kind == MarkdownEventKind.FOOTNOTE_REFERENCE:
            pass
        elif event.kind == MarkdownEventKind.TASK_LIST_MARKER:
            pass

    def prepare_for_event(self, event: MarkdownEvent) -> None:
        if not self.pending_local_link_soft_break:
            return
        if (event.kind == MarkdownEventKind.TEXT
                and event.text.lstrip().startswith(":")):
            self.pending_local_link_soft_break = False
            return
        self.pending_local_link_soft_break = False
        self.push_line(HyperlinkLine.new(""))

    # -- Tag dispatch (port of Writer::start_tag / Writer::end_tag) ------------

    def start_tag(self, event: MarkdownEvent) -> None:
        tag = event.tag
        if tag is None:
            return
        if tag == MarkdownTag.PARAGRAPH:
            self.start_paragraph()
        elif tag == MarkdownTag.HEADING:
            self.start_heading(event.payload)
        elif tag == MarkdownTag.BLOCK_QUOTE:
            self.start_blockquote()
        elif tag == MarkdownTag.CODE_BLOCK:
            payload = event.payload or {}
            self.start_codeblock(payload.get("lang"), payload.get("indent"))
        elif tag == MarkdownTag.LIST:
            self.start_list(event.payload)
        elif tag == MarkdownTag.ITEM:
            self.start_item()
        elif tag == MarkdownTag.EMPHASIS:
            self.push_inline_style(self.styles.emphasis)
        elif tag == MarkdownTag.STRONG:
            self.push_inline_style(self.styles.strong)
        elif tag == MarkdownTag.STRIKETHROUGH:
            self.push_inline_style(self.styles.strikethrough)
        elif tag == MarkdownTag.LINK:
            self.push_link(event.payload or "")
        elif tag == MarkdownTag.TABLE:
            self.start_table(event.payload or [])
        elif tag == MarkdownTag.TABLE_HEAD:
            self.start_table_head()
        elif tag == MarkdownTag.TABLE_ROW:
            self.start_table_row(event)
        elif tag == MarkdownTag.TABLE_CELL:
            self.start_table_cell()

    def end_tag(self, event: MarkdownEvent) -> None:
        tag = event.tag
        if tag is None:
            return
        if tag == MarkdownTag.PARAGRAPH:
            self.end_paragraph()
        elif tag == MarkdownTag.HEADING:
            self.end_heading()
        elif tag == MarkdownTag.BLOCK_QUOTE:
            self.end_blockquote()
        elif tag == MarkdownTag.CODE_BLOCK:
            self.end_codeblock()
        elif tag == MarkdownTag.LIST:
            self.end_list()
        elif tag == MarkdownTag.ITEM:
            self.end_item()
        elif tag in (MarkdownTag.EMPHASIS, MarkdownTag.STRONG, MarkdownTag.STRIKETHROUGH):
            self.pop_inline_style()
        elif tag == MarkdownTag.LINK:
            self.pop_link()
        elif tag == MarkdownTag.TABLE:
            self.end_table()
        elif tag == MarkdownTag.TABLE_HEAD:
            self.end_table_head()
        elif tag == MarkdownTag.TABLE_ROW:
            self.end_table_row()
        elif tag == MarkdownTag.TABLE_CELL:
            self.end_table_cell()

    # -- Block-level events ----------------------------------------------------

    def start_paragraph(self) -> None:
        if self.in_table_cell():
            return
        if self.needs_newline:
            self.push_blank_line()
        self.push_line(HyperlinkLine.new(""))
        self.needs_newline = False
        self.in_paragraph = True

    def end_paragraph(self) -> None:
        if self.in_table_cell():
            return
        self.needs_newline = True
        self.in_paragraph = False
        self.pending_marker_line = False

    def start_heading(self, level) -> None:
        if self.in_table_cell():
            return
        if self.needs_newline:
            self.push_line(HyperlinkLine.new(""))
            self.needs_newline = False
        heading_style = self._heading_style(level)
        prefix = ("# " if level == 1
                  else "## " if level == 2
                  else "### " if level == 3
                  else f"{'#' * level} ")
        self.push_line(HyperlinkLine.new(_style_to_ansi(heading_style, prefix)))
        self.push_inline_style(heading_style)
        self.needs_newline = False

    def end_heading(self) -> None:
        if self.in_table_cell():
            return
        self.needs_newline = True
        self.pop_inline_style()

    def start_blockquote(self) -> None:
        if self.in_table_cell():
            return
        if self.needs_newline:
            self.push_blank_line()
            self.needs_newline = False
        self.indent_stack.append(IndentContext.new(["> "], None, is_list=False))

    def end_blockquote(self) -> None:
        if self.in_table_cell():
            return
        if self.indent_stack:
            self.indent_stack.pop()
        self.needs_newline = True

    def start_list(self, start) -> None:
        if not self.list_indices and self.needs_newline:
            self.push_line(HyperlinkLine.new(""))
        if start is None:
            self.list_indices.append(None)
        else:
            try:
                self.list_indices.append(int(start) - 1)
            except (TypeError, ValueError):
                self.list_indices.append(None)
        self.list_needs_blank_before_next_item.append(False)

    def end_list(self) -> None:
        if self.list_indices:
            self.list_indices.pop()
        if self.list_needs_blank_before_next_item:
            self.list_needs_blank_before_next_item.pop()
        self.needs_newline = True

    def start_item(self) -> None:
        if self.list_needs_blank_before_next_item:
            flag = self.list_needs_blank_before_next_item[-1]
            if flag:
                self.push_blank_line()
                self.list_needs_blank_before_next_item[-1] = False
        self.flush_current_line()
        self.list_item_start_line_counts.append(len(self.output_lines))
        self.pending_marker_line = True
        depth = len(self.list_indices)
        is_ordered = bool(self.list_indices and self.list_indices[-1] is not None)
        width = depth * 4 - 3
        marker: Optional[List[str]]
        if self.list_indices:
            last = self.list_indices[-1]
            if last is None:
                marker = [
                    _style_to_ansi(
                        self.styles.unordered_list_marker,
                        " " * (width - 1) + "- ",
                    )
                ]
            else:
                self.list_indices[-1] = last + 1
                marker = [
                    _style_to_ansi(
                        self.styles.ordered_list_marker,
                        f"{(last + 1):>{width}}. ",
                    )
                ]
        else:
            marker = None
        indent_prefix: List[str]
        if depth == 0:
            indent_prefix = []
        else:
            indent_len = (width + 2) if is_ordered else (width + 1)
            indent_prefix = [" " * indent_len]
        self.indent_stack.append(IndentContext.new(indent_prefix, marker, is_list=True))
        self.needs_newline = False

    def end_item(self) -> None:
        self.flush_current_line()
        start_count = (self.list_item_start_line_counts.pop()
                       if self.list_item_start_line_counts else 0)
        if len(self.output_lines) - start_count > 1 and self.list_needs_blank_before_next_item:
            self.list_needs_blank_before_next_item[-1] = True
        if self.indent_stack:
            self.indent_stack.pop()
        self.pending_marker_line = False

    def start_codeblock(self, lang: Optional[str], indent: Optional[str]) -> None:
        self.flush_current_line()
        if self.output_lines:
            self.push_blank_line()
        self.in_code_block = True
        if lang:
            self.code_block_lang = lang.split()[0] if lang else None
        else:
            self.code_block_lang = None
        self.code_block_buffer = ""
        self.indent_stack.append(IndentContext.new([indent or ""], None, is_list=False))
        self.needs_newline = True

    def end_codeblock(self) -> None:
        if self.code_block_lang:
            code = self.code_block_buffer
            if code:
                for hl_line in highlight_code_to_lines(code, self.code_block_lang):
                    self.push_line(HyperlinkLine.new(hl_line))
        self.needs_newline = True
        self.in_code_block = False
        self.code_block_lang = None
        if self.indent_stack:
            self.indent_stack.pop()

    def start_table(self, alignments: List[str]) -> None:
        self.flush_current_line()
        if self.needs_newline:
            self.push_blank_line()
            self.needs_newline = False
        self.table_state = TableState.new(alignments)

    def end_table(self) -> None:
        ts = self.table_state
        if ts is None:
            return
        self.table_state = None
        result = self._render_table_lines(ts)
        for line in result.table_lines:
            self.push_line(HyperlinkLine.new(line))
        for spill in result.spillover_lines:
            self.push_line(HyperlinkLine.new(spill))
        self.needs_newline = True

    def start_table_head(self) -> None:
        if self.table_state:
            self.table_state.in_header = True
            self.table_state.current_row = []

    def end_table_head(self) -> None:
        ts = self.table_state
        if ts is None:
            return
        if ts.current_cell:
            if ts.current_row is None:
                ts.current_row = []
            ts.current_row.append(ts.current_cell)
            ts.current_cell = None
        if ts.current_row:
            ts.header = ts.current_row
            ts.current_row = None
        ts.in_header = False

    def start_table_row(self, event: MarkdownEvent) -> None:
        if self.table_state:
            self.table_state.current_row = []
            self.table_state.current_row_has_table_pipe_syntax = self._has_table_row_boundary_pipe(event)

    def _has_table_row_boundary_pipe(self, _event: MarkdownEvent) -> bool:
        return False  # markdown-it-py does not expose per-row source text

    def end_table_row(self) -> None:
        ts = self.table_state
        if ts is None:
            return
        if ts.current_cell:
            if ts.current_row is None:
                ts.current_row = []
            ts.current_row.append(ts.current_cell)
            ts.current_cell = None
        if ts.current_row is None:
            return
        if ts.in_header:
            ts.header = ts.current_row
        else:
            ts.rows.append(TableBodyRow(
                cells=ts.current_row,
                has_table_pipe_syntax=ts.current_row_has_table_pipe_syntax,
            ))
        ts.current_row = None
        ts.current_row_has_table_pipe_syntax = False

    def start_table_cell(self) -> None:
        if self.table_state:
            self.table_state.current_cell = TableCell()

    def end_table_cell(self) -> None:
        ts = self.table_state
        if ts is None:
            return
        if ts.current_cell:
            if ts.current_row is None:
                ts.current_row = []
            ts.current_row.append(ts.current_cell)
            ts.current_cell = None

    def in_table_cell(self) -> bool:
        ts = self.table_state
        return ts is not None and ts.current_cell is not None

    # -- Inline / link helpers (port of Writer text/code/html methods) ---------

    def text(self, text_str: str) -> None:
        if self.suppressing_local_link_label():
            return
        self.line_ends_with_local_link_target = False
        if self.in_table_cell():
            self._push_text_to_table_cell(text_str)
            return
        if self.pending_marker_line:
            self.pending_marker_line = False
            style = self.inline_styles[-1] if self.inline_styles else Style()
            if self.output_lines:
                self.output_lines[-1].line += _style_to_ansi(style, text_str)
                return
        self.pending_marker_line = False

        if self.in_code_block and self.code_block_lang is not None:
            self.code_block_buffer += text_str
            return

        if self.in_code_block and not self.needs_newline:
            has_content = bool(
                self.current_line_content
                and self.current_line_content.line
            ) or bool(
                self.output_lines and self.output_lines[-1].line
            )
            if has_content:
                self.push_line(HyperlinkLine.new(""))

        for i, line in enumerate(text_str.split("\n")):
            if self.needs_newline:
                self.push_line(HyperlinkLine.new(""))
                self.needs_newline = False
            if i > 0:
                self.push_line(HyperlinkLine.new(""))
            style = self.inline_styles[-1] if self.inline_styles else Style()
            self._push_text_spans(line, style)
        self.needs_newline = False

    def code(self, code_str: str) -> None:
        if self.suppressing_local_link_label():
            return
        self.line_ends_with_local_link_target = False
        if self.in_table_cell():
            self._push_span_to_table_cell(_style_to_ansi(self.styles.code, code_str))
            return
        if self.pending_marker_line:
            self.pending_marker_line = False
        self._push_span(_style_to_ansi(self.styles.code, code_str))

    def html(self, html_str: str, inline: bool) -> None:
        if self.suppressing_local_link_label():
            return
        self.line_ends_with_local_link_target = False
        if self.in_table_cell():
            style = self.inline_styles[-1] if self.inline_styles else Style()
            for i, line in enumerate(html_str.split("\n")):
                if i > 0:
                    self._push_table_cell_hard_break()
                self._push_span_to_table_cell(_style_to_ansi(style, line))
            if not inline:
                self._push_table_cell_hard_break()
            return
        self.pending_marker_line = False
        for i, line in enumerate(html_str.split("\n")):
            if self.needs_newline:
                self.push_line(HyperlinkLine.new(""))
                self.needs_newline = False
            if i > 0:
                self.push_line(HyperlinkLine.new(""))
            style = self.inline_styles[-1] if self.inline_styles else Style()
            self._push_span(_style_to_ansi(style, line))
        self.needs_newline = not inline

    def hard_break(self) -> None:
        if self.suppressing_local_link_label():
            return
        self.line_ends_with_local_link_target = False
        if self.in_table_cell():
            self._push_table_cell_hard_break()
            return
        self.push_line(HyperlinkLine.new(""))

    def soft_break(self) -> None:
        if self.suppressing_local_link_label():
            return
        if self.in_table_cell():
            style = self.inline_styles[-1] if self.inline_styles else Style()
            self._push_span_to_table_cell(_style_to_ansi(style, " "))
            return
        if self.line_ends_with_local_link_target:
            self.pending_local_link_soft_break = True
            self.line_ends_with_local_link_target = False
            return
        self.line_ends_with_local_link_target = False
        self.push_line(HyperlinkLine.new(""))

    def push_link(self, dest_url: str) -> None:
        """Port of Writer's Tag::Link handler."""
        local_display: Optional[str] = None
        if is_local_path_like_link(dest_url):
            local_display = render_local_link_target(dest_url, self.cwd)
        self.link = LinkState(
            destination=dest_url,
            show_destination=True,
            local_target_display=local_display,
        )

    def pop_link(self) -> None:
        """Port of Writer's TagEnd::Link handler."""
        if self.link and self.link.local_target_display is not None:
            self._emit_text(self.link.local_target_display)
            self.line_ends_with_local_link_target = True
        self.link = None

    def push_inline_style(self, style: Style) -> None:
        self.inline_styles.append(style)

    def pop_inline_style(self) -> None:
        if self.inline_styles:
            self.inline_styles.pop()

    def suppressing_local_link_label(self) -> bool:
        return self.link is not None and self.link.local_target_display is not None

    def _emit_text(self, text_str: str) -> None:
        if not text_str:
            return
        if self.link and self.link.show_destination and should_render_link_destination(self.link.destination):
            self._emit_text_raw(f"{text_str} ({self.link.destination})")
            return
        if self.link and is_local_path_like_link(self.link.destination) and self.link.local_target_display is not None:
            self._emit_text_raw(self.link.local_target_display)
            self.line_ends_with_local_link_target = True
            return
        style = self.inline_styles[-1] if self.inline_styles else Style()
        self._emit_text_raw(_style_to_ansi(style, text_str))

    def _emit_text_raw(self, text_str: str) -> None:
        if self.needs_newline:
            self.push_line(HyperlinkLine.new(""))
            self.needs_newline = False
        if not self.output_lines:
            self.push_line(HyperlinkLine.new(""))
        if self.output_lines:
            self.output_lines[-1].line = self.output_lines[-1].line + text_str
            self._note_output_line()

    def _push_text_spans(self, text_str: str, style: Style) -> None:
        """Port of Writer::push_text_spans."""
        if self.needs_newline:
            self.push_line(HyperlinkLine.new(""))
            self.needs_newline = False
        if not self.output_lines:
            self.push_line(HyperlinkLine.new(""))
        if self.output_lines:
            self.output_lines[-1].line = self.output_lines[-1].line + _style_to_ansi(style, text_str)
            self._note_output_line()

    def _push_span(self, span: str) -> None:
        if self.needs_newline:
            self.push_line(HyperlinkLine.new(""))
            self.needs_newline = False
        if not self.output_lines:
            self.push_line(HyperlinkLine.new(""))
        if self.output_lines:
            self.output_lines[-1].line = self.output_lines[-1].line + span
            self._note_output_line()

    def _push_text_to_table_cell(self, text_str: str) -> None:
        style = self.inline_styles[-1] if self.inline_styles else Style()
        for i, line in enumerate(text_str.split("\n")):
            if i > 0:
                self._push_table_cell_hard_break()
            self._push_text_spans_to_table_cell(line, style)

    def _push_text_spans_to_table_cell(self, text_str: str, style: Style) -> None:
        ts = self.table_state
        if ts is None or ts.current_cell is None:
            return
        ts.current_cell.push_span(_style_to_ansi(style, text_str))

    def _push_span_to_table_cell(self, span: str) -> None:
        ts = self.table_state
        if ts is None or ts.current_cell is None:
            return
        ts.current_cell.push_span(span)

    def _push_table_cell_hard_break(self) -> None:
        ts = self.table_state
        if ts is None or ts.current_cell is None:
            return
        ts.current_cell.hard_break()

    # -- Line helpers ----------------------------------------------------------

    def push_line(self, line: HyperlinkLine) -> None:
        prefix = self._prefix_spans()
        if prefix:
            line.line = prefix + line.line
        self.output_lines.append(line)
        self.current_line_content = line
        self._note_output_line()

    def push_blank_line(self) -> None:
        self.output_lines.append(HyperlinkLine.new(""))

    def push_prewrapped_line(self, line: HyperlinkLine, needs_marker_line: bool) -> None:
        """Port of Writer::push_prewrapped_line."""
        if needs_marker_line:
            self.push_line(HyperlinkLine.new(""))
        self.push_hyperlink_line(line)
        self.flush_current_line()

    def push_hyperlink_line(self, line: HyperlinkLine) -> None:
        """Port of Writer::push_hyperlink_line."""
        if self.needs_newline:
            self.push_line(HyperlinkLine.new(""))
            self.needs_newline = False
        if not self.output_lines:
            self.push_line(HyperlinkLine.new(""))
        if self.output_lines:
            self.output_lines[-1].line = self.output_lines[-1].line + line.line
            self._note_output_line()

    def flush_current_line(self) -> None:
        if self.current_line_content and not self.current_line_content.line:
            self.current_line_content = None

    def _prefix_spans(self) -> str:
        prefix = ""
        for ctx in self.indent_stack:
            if ctx.marker:
                prefix += "".join(ctx.marker)
            else:
                prefix += "".join(ctx.prefix)
        return prefix

    def _heading_style(self, level) -> Style:
        return [
            self.styles.h1, self.styles.h2, self.styles.h3,
            self.styles.h4, self.styles.h5, self.styles.h6,
        ][max(0, min(5, (level or 1) - 1))]

    # -- Table layout (port of render_table_lines) -----------------------------

    def _render_table_lines(self, table_state: TableState) -> RenderedTableLines:
        column_count = max(
            1,
            len(table_state.alignments) or
            (len(table_state.header) if table_state.header else 0) or
            (len(table_state.rows[0].cells) if table_state.rows else 0),
        )
        if column_count == 0:
            return RenderedTableLines(
                table_lines=[], table_lines_prewrapped=True, spillover_lines=[]
            )

        spillover_rows: List[TableCell] = []
        rows: List[List[TableCell]] = []
        for idx, row in enumerate(table_state.rows):
            next_row = table_state.rows[idx + 1] if idx + 1 < len(table_state.rows) else None
            if column_count > 1 and self._is_spillover_row(row, next_row):
                if row.cells:
                    spillover_rows.append(row.cells[0])
            else:
                rows.append(list(row.cells))

        header = (table_state.header
                  if table_state.header is not None
                  else [TableCell() for _ in range(column_count)])
        self._normalize_row(header, column_count)
        for r in rows:
            self._normalize_row(r, column_count)

        available = self._available_table_width(column_count)
        widths = self._compute_column_widths(header, rows, table_state.alignments, available, column_count)
        spillover_lines: List[str] = []
        for spill in spillover_rows:
            spillover_lines.extend(spill.lines)

        if widths is None:
            if rows:
                return self._render_records(header, rows, available)
            return RenderedTableLines(
                table_lines=self._render_table_pipe_fallback(header, rows, table_state.alignments),
                table_lines_prewrapped=False,
                spillover_lines=spillover_lines,
            )

        if self._should_render_records(rows, widths):
            return self._render_records(header, rows, self._available_record_width())

        out: List[str] = []
        out.extend(self._render_table_row(header, widths, table_state.alignments, self.styles.strong))
        out.append(self._render_table_separator(widths, TABLE_HEADER_SEPARATOR_CHAR, Style(), "┝", "┿", "┥"))
        for idx, row in enumerate(rows):
            out.extend(self._render_table_row(row, widths, table_state.alignments, Style()))
            if idx + 1 < len(rows):
                out.append(self._render_table_separator(widths, TABLE_BODY_SEPARATOR_CHAR, Style(), "├", "┼", "┤"))
        return RenderedTableLines(
            table_lines=out, table_lines_prewrapped=True, spillover_lines=spillover_lines
        )

    def _normalize_row(self, row: List[TableCell], column_count: int) -> None:
        del row[column_count:]
        while len(row) < column_count:
            row.append(TableCell())

    def _available_table_width(self, column_count: int) -> Optional[int]:
        if self.wrap_width is None:
            return None
        prefix_width = _visible_width(self._prefix_spans())
        reserved = prefix_width + (column_count - 1) * TABLE_COLUMN_GAP + (column_count * TABLE_CELL_PADDING * 2) + 2
        return max(0, self.wrap_width - reserved)

    def _available_record_width(self) -> Optional[int]:
        if self.wrap_width is None:
            return None
        prefix_width = _visible_width(self._prefix_spans())
        return max(0, self.wrap_width - prefix_width)

    def _render_records(
        self,
        header: List[TableCell],
        rows: List[List[TableCell]],
        available: Optional[int],
    ) -> RenderedTableLines:
        """Vertical key/value fallback — port of table_key_value::render_records.

        Calls the ported heuristic in ``table_key_value.render_records``.
        """
        from .table_key_value import render_records as _render_records_kv
        metrics = self._collect_table_column_metrics(
            header, rows, len(header)
        )
        link_lines = _render_records_kv(
            header,
            rows,
            metrics,
            available,
            self.styles.strong,
            Style(),
        )
        return RenderedTableLines(
            table_lines=[line.line for line in link_lines],
            table_lines_prewrapped=True,
            spillover_lines=[],
        )

    def _should_render_records(
        self,
        rows: List[List[TableCell]],
        widths: List[int],
    ) -> bool:
        """Port of table_key_value::should_render_records (mode-switch threshold)."""
        from .table_key_value import should_render_records as _should_render_records_kv
        if not rows or not widths:
            return False
        metrics = self._collect_table_column_metrics(
            rows[0] if rows else [TableCell() for _ in widths],
            rows,
            len(widths),
        )
        return _should_render_records_kv(rows, widths, metrics)

    # -- Column width allocation (port of compute_column_widths + helpers) ---

    def _compute_column_widths(
        self,
        header: List[TableCell],
        rows: List[List[TableCell]],
        alignments: List[str],
        available_width: Optional[int],
        column_count: int = 0,
    ) -> Optional[List[int]]:
        """Port of Writer::compute_column_widths (full algorithm with metrics)."""
        min_column_width = 3
        col_count = len(alignments) or column_count
        metrics = self._collect_table_column_metrics(header, rows, col_count)
        widths: List[int] = [
            max(metrics[i].max_width, min_column_width)
            for i in range(col_count)
        ]
        if available_width is None:
            return widths
        if available_width < min_column_width * col_count:
            return None

        floors: List[int] = [
            self._preferred_column_floor(metrics[i], min_column_width)
            for i in range(col_count)
        ]
        floor_total = sum(floors)
        while floor_total > available_width:
            candidate = None
            candidate_priority = None
            for idx, floor in enumerate(floors):
                if floor <= min_column_width:
                    continue
                priority = self._column_shrink_priority(metrics[idx].kind)
                if candidate is None or priority < candidate_priority or (
                    priority == candidate_priority
                    and (min_column_width - floor) > (min_column_width - candidate[1])
                ):
                    candidate = (idx, floor)
                    candidate_priority = priority
            if candidate is None:
                break
            floors[candidate[0]] -= 1
            floor_total -= 1

        total_width = sum(widths)
        while total_width > available_width:
            idx = self._next_column_to_shrink(widths, floors, metrics)
            if idx is None:
                break
            widths[idx] -= 1
            total_width -= 1

        if total_width > available_width:
            return None
        return widths

    @staticmethod
    def _collect_table_column_metrics(
        header: List[TableCell],
        rows: List[List[TableCell]],
        column_count: int,
    ) -> List[TableColumnMetrics]:
        """Port of Writer::collect_table_column_metrics."""
        metrics: List[TableColumnMetrics] = []
        for column in range(column_count):
            header_cell = header[column]
            header_plain = header_cell.plain_text()
            header_token_width = Writer._longest_token_width(header_plain)
            max_width = Writer._cell_display_width(header_cell)
            body_token_width = 0
            body_token_count = 0
            long_body_token_count = 0
            total_words = 0
            total_cells = 0
            total_cell_width = 0
            for row in rows:
                cell = row[column]
                max_width = max(max_width, Writer._cell_display_width(cell))
                plain = cell.plain_text()
                body_token_width = max(
                    body_token_width, Writer._longest_token_width(plain)
                )
                word_count = len(plain.split())
                if word_count > 0:
                    body_token_count += word_count
                    long_body_token_count += sum(
                        1 for token in plain.split()
                        if _visible_width(token) >= 20
                    )
                    total_words += word_count
                    total_cells += 1
                    total_cell_width += _visible_width(plain)
            if total_cells == 0:
                avg_words_per_cell = float(len(header_plain.split()))
                avg_cell_width = float(_visible_width(header_plain))
            else:
                avg_words_per_cell = total_words / total_cells
                avg_cell_width = total_cell_width / total_cells
            if (long_body_token_count > 0
                    and long_body_token_count >= body_token_count - long_body_token_count):
                kind = TableColumnKind.TokenHeavy
            elif avg_words_per_cell >= 4.0 or avg_cell_width >= 28.0:
                kind = TableColumnKind.Narrative
            else:
                kind = TableColumnKind.Compact
            metrics.append(TableColumnMetrics(
                max_width=max_width,
                header_token_width=header_token_width,
                body_token_width=body_token_width,
                kind=kind,
            ))
        return metrics

    @staticmethod
    def _preferred_column_floor(
        metrics: TableColumnMetrics, min_column_width: int
    ) -> int:
        """Port of Writer::preferred_column_floor."""
        if metrics.kind in (TableColumnKind.Narrative, TableColumnKind.TokenHeavy):
            token_target = 16
        else:
            token_target = max(
                metrics.header_token_width,
                min(metrics.body_token_width, 16),
            )
        return min(max(token_target, min_column_width), metrics.max_width)

    @staticmethod
    def _next_column_to_shrink(
        widths: List[int],
        floors: List[int],
        metrics: List[TableColumnMetrics],
    ) -> Optional[int]:
        """Port of Writer::next_column_to_shrink (TokenHeavy → Narrative → Compact)."""
        best_idx: Optional[int] = None
        best_key: Optional[tuple] = None
        for idx, width in enumerate(widths):
            if width <= floors[idx]:
                continue
            slack = width - floors[idx]
            key = (
                Writer._column_shrink_priority(metrics[idx].kind),
                -slack,
            )
            if best_key is None or key < best_key:
                best_key = key
                best_idx = idx
        return best_idx

    @staticmethod
    def _column_shrink_priority(kind: str) -> int:
        """Port of Writer::column_shrink_priority (TokenHeavy=0, Narrative=1, Compact=2)."""
        if kind == TableColumnKind.TokenHeavy:
            return 0
        if kind == TableColumnKind.Narrative:
            return 1
        return 2

    @staticmethod
    def _cell_display_width(cell: TableCell) -> int:
        """Port of Writer::cell_display_width (max line display width)."""
        if not cell.lines:
            return 0
        return max(_visible_width(line) for line in cell.lines)

    @staticmethod
    def _line_display_width(line: str) -> int:
        """Port of Writer::line_display_width (Unicode-width of a line)."""
        return _visible_width(line)

    @staticmethod
    def _longest_token_width(text: str) -> int:
        """Port of Writer::longest_token_width."""
        tokens = text.split()
        if not tokens:
            return 0
        return max(_visible_width(t) for t in tokens)

    def _render_table_separator(
        self,
        column_widths: List[int],
        separator_char: str,
        style: Style,
        left: str = "├",
        cross: str = "┼",
        right: str = "┤",
    ) -> str:
        """Port of Writer::render_table_separator with box-drawing crossings."""
        parts: List[str] = []
        for i, w in enumerate(column_widths):
            if i == 0:
                parts.append(left)
            else:
                parts.append(cross)
            parts.append(separator_char * (w + (TABLE_CELL_PADDING * 2)))
        parts.append(right)
        return _style_to_ansi(style, "".join(parts))

    def _render_table_row(
        self,
        row: List[TableCell],
        column_widths: List[int],
        alignments: List[str],
        row_style: Style,
    ) -> List[str]:
        """Port of Writer::render_table_row with Left/Right/Center alignment."""
        wrapped_cells: List[List[HyperlinkLine]] = [
            self._wrap_cell(cell, width)
            for cell, width in zip(row, column_widths)
        ]
        row_height = max((len(c) for c in wrapped_cells), default=1)
        out: List[str] = []
        for row_line in range(row_height):
            if not any(
                row_line < len(cell_lines)
                and _visible_width(cell_lines[row_line].line) > 0
                for cell_lines in wrapped_cells
            ):
                out.append("")
                continue
            parts: List[str] = []
            for column in range(len(column_widths)):
                width = column_widths[column]
                if column > 0:
                    parts.append(" " * TABLE_COLUMN_GAP)
                parts.append(" " * TABLE_CELL_PADDING)
                cell_line = (
                    wrapped_cells[column][row_line]
                    if row_line < len(wrapped_cells[column])
                    else HyperlinkLine.new("")
                )
                line_width = _visible_width(cell_line.line)
                remaining = max(0, width - line_width)
                align = alignments[column] if column < len(alignments) else "none"
                if align in ("left", "none"):
                    left_pad, right_pad = 0, remaining
                elif align == "center":
                    left_pad, right_pad = remaining // 2, remaining - (remaining // 2)
                else:
                    left_pad, right_pad = remaining, 0
                if left_pad > 0:
                    parts.append(" " * left_pad)
                parts.append(cell_line.line)
                if right_pad > 0:
                    parts.append(" " * right_pad)
                parts.append(" " * TABLE_CELL_PADDING)
            line = "│" + "".join(parts) + "│"
            out.append(_style_to_ansi(row_style, line))
        return out

    def _wrap_cell(
        self,
        cell: TableCell,
        width: int,
    ) -> List[HyperlinkLine]:
        """Port of Writer::wrap_cell (preserves hyperlinks across wrapped lines)."""
        if not cell.lines:
            return [HyperlinkLine.new("")]
        wrapped: List[HyperlinkLine] = []
        for source_text in cell.lines:
            source = HyperlinkLine.new(source_text)
            if _ANSI_RE.search(source_text):
                rendered = _ansi_safe_wrap_line(source_text, max(1, width))
            else:
                rendered = word_wrap_line(source_text, RtOptions(width=max(1, width)))
                rendered = [_line_to_static(r) for r in rendered]
            if not rendered:
                wrapped.append(HyperlinkLine.new(""))
            else:
                wrapped.extend(remap_wrapped_line(source, rendered))
        if not wrapped:
            wrapped.append(HyperlinkLine.new(""))
        return wrapped

    def _render_table_pipe_fallback(
        self,
        header: List[TableCell],
        rows: List[List[TableCell]],
        alignments: List[str],
    ) -> List[str]:
        """Port of Writer::render_table_pipe_fallback (header-only fallback)."""
        out: List[str] = []
        out.append(self._row_to_pipe_line(header).line)
        out.append(self._alignments_to_pipe_delimiter(alignments))
        for row in rows:
            out.append(self._row_to_pipe_line(row).line)
        return out

    @staticmethod
    def _row_to_pipe_line(row: List[TableCell]) -> HyperlinkLine:
        """Port of Writer::row_to_pipe_line (escapes ``|`` as ``\\|``)."""
        out = HyperlinkLine.new("")
        out.push_span("│", None)
        for cell in row:
            out.push_span(" ", None)
            for index, line_text in enumerate(cell.lines):
                if index > 0:
                    out.push_span(" ", None)
                column = 0
                for ch in line_text:
                    if ch == "|":
                        out.push_span("│", None)
                    else:
                        out.push_span(ch, None)
                    column += _char_width(ch)
            out.push_span(" │", None)
        return out

    @staticmethod
    def _alignments_to_pipe_delimiter(alignments: List[str]) -> str:
        """Port of Writer::alignments_to_pipe_delimiter."""
        out = ["│"]
        for alignment in alignments:
            if alignment == "left":
                out.append(":───│")
            elif alignment == "center":
                out.append(":───:│")
            elif alignment == "right":
                out.append("───:│")
            else:
                out.append("───│")
        return "".join(out)

    @staticmethod
    def _alignment_to_pipe(alignment: str) -> str:
        if alignment == "left":
            return ":───"
        if alignment == "center":
            return ":───:"
        if alignment == "right":
            return "───:"
        return "───"

    def _is_spillover_row(self, row: TableBodyRow, next_row: Optional[TableBodyRow]) -> bool:
        """Port of Writer::is_spillover_row heuristic."""
        first_text = self._first_non_empty_only_text(row.cells)
        if first_text is None:
            return False
        if len(row.cells) == 1 and not row.has_table_pipe_syntax:
            return True
        if self._looks_like_html_content(first_text):
            return True
        if first_text.rstrip().endswith(":"):
            if next_row is not None:
                next_text = self._first_non_empty_only_text(next_row.cells)
                if next_text is not None and self._looks_like_html_content(next_text):
                    return True
            if next_row is None and self._looks_like_html_label_line(first_text):
                return True
        return False

    def _first_non_empty_only_text(self, row_cells: List[TableCell]) -> Optional[str]:
        if not row_cells:
            return None
        first = row_cells[0].plain_text()
        if first.strip() == "":
            return None
        if not all(c.plain_text().strip() == "" for c in row_cells[1:]):
            return None
        return first

    def _looks_like_html_content(self, text: str) -> bool:
        for idx, byte in enumerate(text.encode("ascii", errors="ignore")):
            if byte != ord("<"):
                continue
            tag_start = idx + 1
            raw = text.encode("ascii", errors="ignore")
            if tag_start < len(raw) and raw[tag_start] in (ord("/"), ord("!")):
                tag_start += 1
            if (tag_start < len(raw) and raw[tag_start:tag_start + 1].isalpha()
                    and b">" in raw[tag_start + 1:]):
                return True
        return False

    def _looks_like_html_label_line(self, text: str) -> bool:
        trimmed = text.strip()
        if not trimmed.endswith(":"):
            return False
        prefix = trimmed.rstrip(":").strip()
        return any(w.lower() == "html" for w in prefix.split())


# -- Helpers -------------------------------------------------------------------

def _style_to_ansi(style: Style, text: str) -> str:
    """Render a ``Style + text`` pair to an ANSI-styled string."""
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
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _ansi_safe_wrap_line(text: str, width: int) -> List[str]:
    """Hard-wrap an ANSI-styled line without splitting escape sequences."""
    width = max(1, width)
    parts = re.split(r"(\033\[[0-9;]*m)", text)
    lines: List[str] = []
    current: List[str] = []
    visible = 0
    active_style = ""
    for part in parts:
        if not part:
            continue
        if _ANSI_RE.fullmatch(part):
            current.append(part)
            active_style = "" if part == "\033[0m" else part
            continue
        for ch in part:
            if visible >= width:
                if active_style:
                    current.append("\033[0m")
                lines.append("".join(current))
                current = [active_style] if active_style else []
                visible = 0
                if ch == " ":
                    continue
            current.append(ch)
            visible += 1
    if current or not lines:
        if active_style:
            current.append("\033[0m")
        lines.append("".join(current))
    return lines


def _ansi_style_atom_stream(text: str) -> Tuple[List[str], List[str]]:
    """Split an ANSI-styled line into visible chars + per-char active SGR.

    Returns ``(chars, styles)`` where ``styles[i]`` is the SGR sequence
    that is active before character ``i`` (``""`` when no style is open).
    The renderer wraps every styled span as ``ESC[..m text ESC[0m``, so
    tracking the last non-reset SGR per character is sufficient.
    """
    chars: List[str] = []
    styles: List[str] = []
    active = ""
    pos = 0
    for match in _ANSI_RE.finditer(text):
        for ch in text[pos:match.start()]:
            chars.append(ch)
            styles.append(active)
        code = match.group(0)
        active = "" if code == "\x1b[0m" else code
        pos = match.end()
    for ch in text[pos:]:
        chars.append(ch)
        styles.append(active)
    return chars, styles


def _styled_slice(
    chars: List[str], styles: List[str], start: int, end: int
) -> str:
    """Rebuild an ANSI-styled substring from ``chars``/``styles`` ranges."""
    parts: List[str] = []
    active = ""
    for i in range(start, end):
        style = styles[i]
        if style != active:
            if active:
                parts.append("\x1b[0m")
            if style:
                parts.append(style)
            active = style
        parts.append(chars[i])
    if active:
        parts.append("\x1b[0m")
    return "".join(parts)


def _ansi_safe_wrap_line_to_width(
    text: str, width: int, subsequent_indent: str = ""
) -> List[str]:
    """Word-wrap an ANSI-styled line to ``width`` visible columns.

    Unlike :func:`_ansi_safe_wrap_line` (hard character wrap), this
    keeps words intact, matching the Rust ``flush_current_line`` path
    (``adaptive_wrap_line``). SGR state is reopened on continuation
    lines so styled spans survive a line break.
    """
    width = max(1, width)
    if _visible_width(text) <= width:
        return [text]
    chars, styles = _ansi_style_atom_stream(text)
    plain = "".join(chars)
    if not plain:
        return [text]
    ranges = wrap_ranges_trim(
        plain,
        RtOptions(
            width=width,
            initial_indent="",
            subsequent_indent=subsequent_indent,
        ),
    )
    out: List[str] = []
    for idx, (start, end) in enumerate(ranges):
        prefix = "" if idx == 0 else subsequent_indent
        out.append(prefix + _styled_slice(chars, styles, start, end))
    return out or [text]


def _leading_whitespace_visible(text: str) -> str:
    """Visible leading space run of a line, ignoring ANSI escapes."""
    visible = _ANSI_RE.sub("", text)
    return visible[: len(visible) - len(visible.lstrip(" "))]


def _visible_width(text: str) -> int:
    """Display width of a string with ANSI codes stripped."""
    return len(_ANSI_RE.sub("", text))


def _visible_trim(text: str, width: int) -> str:
    """Trim text to visible width, keeping ANSI codes intact."""
    visible = _ANSI_RE.sub("", text)
    if len(visible) <= width:
        return visible
    return visible[:width]


def _wrap_text(text: str, width: int) -> List[str]:
    """Simple word-wrap to fit ``width`` visible chars."""
    if width <= 0 or not text:
        return text.split("\n") if text else [""]
    words = text.split()
    if not words:
        return [""]
    lines: List[str] = []
    current = ""
    for w in words:
        if not current:
            current = w
        elif len(current) + 1 + len(w) <= width:
            current = current + " " + w
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def _pad_right(text: str, width: int) -> str:
    return text + " " * max(0, width - len(text))


def _visible_lines(lines: List[HyperlinkLine]) -> List[str]:
    """Collapse HyperlinkLines to plain strings — port of
    ``terminal_hyperlinks::visible_lines``."""
    return [line.line for line in lines]


__all__ = [
    "MarkdownStyles",
    "MarkdownEvent",
    "MarkdownEventKind",
    "MarkdownTag",
    "HeadingLevel",
    "CodeBlockKind",
    "IndentContext",
    "TableCell",
    "TableBodyRow",
    "TableState",
    "RenderedTableLines",
    "TableColumnKind",
    "TableColumnMetrics",
    "LinkState",
    "HyperlinkLine",
    "TABLE_COLUMN_GAP",
    "TABLE_CELL_PADDING",
    "TABLE_HEADER_SEPARATOR_CHAR",
    "TABLE_BODY_SEPARATOR_CHAR",
    "should_render_link_destination",
    "is_local_path_like_link",
    "render_local_link_target",
    "parse_local_link_target",
    "normalize_hash_location_suffix_fragment",
    "extract_colon_location_suffix",
    "expand_local_link_path",
    "file_url_to_local_path_text",
    "normalize_local_link_path_text",
    "is_absolute_local_link_path",
    "trim_trailing_local_path_separator",
    "strip_local_path_prefix",
    "display_local_link_path",
    "render_markdown_text",
    "render_markdown_text_with_width",
    "render_markdown_text_with_width_and_cwd",
    "render_markdown_lines_with_width_and_cwd",
    "Writer",
    "markdown_it_to_events",
]


# ---------------------------------------------------------------------------
# Inline formatter (legacy compatibility shim)
# ---------------------------------------------------------------------------
# Added so `from ..markdown_render import _format_inline` (used by the
# approval_overlay legacy code path) keeps working while we finish the
# one-to-one port. The full Codex `format_inline` lives in a separate module
# in codex — for now we provide a minimal Python implementation that
# passes through the text without modification.

def _format_inline(text: str) -> str:
    """Inline formatter used by legacy approval_overlay.

    Minimal Python port: passes text through unchanged. The full Codex
    implementation strips / re-renders inline markdown (links, code,
    emphasis) inside a single line; the legacy call sites do not depend
    on the visual transformation, only on receiving a string back.
    """
    return text


# ---------------------------------------------------------------------------
# Legacy compatibility shim (used by cells/history_cell.py)
# ---------------------------------------------------------------------------
# Added so `from ..markdown_render import render_markdown_to_lines` (used
# by the legacy history_cell rendering path) keeps working while the
# full one-to-one port lands. The full Codex implementation lives in
# `markdown.rs`; this shim delegates to `render_markdown_text` which is
# the equivalent one-to-one entry point in our port.

def render_markdown_to_lines(input: str, width: Optional[int] = None) -> List[str]:
    """Render markdown ``input`` to a list of ANSI-styled lines.

    Convenience alias for :func:`render_markdown_text_with_width` (and
    :func:`render_markdown_text` when ``width`` is None). Mirrors the
    legacy cell-rendering call site.
    """
    if re.fullmatch(r"\s*\d+\.\s*", input or ""):
        return [(input or "").strip()]
    if width is None:
        return render_markdown_text(input)
    return render_markdown_text_with_width(input, width)
