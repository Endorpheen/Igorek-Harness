"""Vertical key/value rendering for markdown tables that no longer scan well as grids.

Python port of codex-rs/tui/src/markdown_render/table_key_value.rs.

The Python analogue of ratatui's ``Line``/``Span`` is a plain ``str``;
styles are applied through the ``Style`` dataclass from
``render.highlight`` and converted to ANSI by the upstream markdown
renderer.
"""
from __future__ import annotations

from typing import List, Optional

from ..render.highlight import Style
from ..render.line_utils import line_to_static
from ..terminal_hyperlinks import (
    HyperlinkLine,
    remap_wrapped_line,
)
from ..wrapping import RtOptions, word_wrap_line
from . import (
    TABLE_BODY_SEPARATOR_CHAR,
    TableCell,
    TableColumnKind,
    TableColumnMetrics,
)


# Field layout constants (port one-to-one)
FIELD_LEADING_PADDING: int = 1
FIELD_GAP: int = 2
MIN_VALUE_WIDTH: int = 3
MIN_ALIGNED_COMPACT_VALUE_WIDTH: int = 12
MIN_ALIGNED_EXPANSIVE_VALUE_WIDTH: int = 24
MIN_SCANNABLE_NARRATIVE_WIDTH: int = 12
MIN_SCANNABLE_TOKEN_HEAVY_WIDTH: int = 12
CRAMPED_EXPANSIVE_CELL_LINES: int = 4
CATASTROPHIC_NARRATIVE_CELL_LINES: int = 7
STACKED_VALUE_INDENT: int = 2


# -- Threshold switch (port of should_render_records) --------------------------

def should_render_records(
    rows: List[List[TableCell]],
    column_widths: List[int],
    metrics: List[TableColumnMetrics],
) -> bool:
    """Switch modes after enough records contain values the grid can no longer present.

    Port of ``codex::markdown_render::table_key_value::should_render_records``.
    """
    if not rows:
        return False

    def _row_is_affected(row: List[TableCell]) -> bool:
        contains_fragmented_value = False
        for cell, width, m in zip(row, column_widths, metrics):
            has_fragmented_token = any(
                _display_width(token) > width
                for token in cell.plain_text().split()
            )
            if m.kind == TableColumnKind.Compact and has_fragmented_token:
                contains_fragmented_value = True
            elif m.kind == TableColumnKind.TokenHeavy:
                if width < MIN_SCANNABLE_TOKEN_HEAVY_WIDTH and has_fragmented_token:
                    contains_fragmented_value = True
            elif m.kind == TableColumnKind.Narrative:
                pass
        return contains_fragmented_value or _expansive_cells_are_starved(row, column_widths, metrics)

    affected_rows = sum(1 for row in rows if _row_is_affected(row))
    if len(rows) == 1:
        threshold = 1
    else:
        threshold = max(2, -(-len(rows) // 3))  # div_ceil(len, 3)
    return affected_rows >= threshold


def _expansive_cells_are_starved(
    row: List[TableCell],
    column_widths: List[int],
    metrics: List[TableColumnMetrics],
) -> bool:
    expansive: List[tuple] = []
    for cell, width, m in zip(row, column_widths, metrics):
        if m.kind == TableColumnKind.Compact:
            continue
        height = len(_wrap_cell(cell, width))
        expansive.append((m.kind, width, height))

    if sum(1 for _, _, h in expansive if h >= CRAMPED_EXPANSIVE_CELL_LINES) >= 2:
        return True
    return any(
        kind == TableColumnKind.Narrative
        and width < MIN_SCANNABLE_NARRATIVE_WIDTH
        and height >= CATASTROPHIC_NARRATIVE_CELL_LINES
        for kind, width, height in expansive
    )


# -- Record rendering (port of render_records) --------------------------------

def render_records(
    headers: List[TableCell],
    rows: List[List[TableCell]],
    metrics: List[TableColumnMetrics],
    available_width: Optional[int],
    label_style: Style,
    separator_style: Style,
) -> List[HyperlinkLine]:
    """Render records in vertical key/value form (port one-to-one)."""
    label_width = max(
        (_display_width(header.plain_text()) for header in headers),
        default=0,
    )
    minimum_value_width = (
        MIN_ALIGNED_EXPANSIVE_VALUE_WIDTH
        if any(m.kind != TableColumnKind.Compact for m in metrics)
        else MIN_ALIGNED_COMPACT_VALUE_WIDTH
    )
    aligned_fields = available_width is None or (
        FIELD_LEADING_PADDING + label_width + FIELD_GAP + minimum_value_width
        <= available_width
    )

    out: List[HyperlinkLine] = []
    for row_index, row in enumerate(rows):
        for header, value in zip(headers, row):
            if aligned_fields:
                _render_aligned_field(
                    out, header, value, label_width, available_width, label_style
                )
            else:
                _render_stacked_field(
                    out, header, value, available_width, label_style
                )
        if row_index + 1 < len(rows):
            width = (
                available_width
                if available_width is not None
                else _widest_line_width(out)
            )
            sep_text = TABLE_BODY_SEPARATOR_CHAR * width
            sep_line = HyperlinkLine.new(sep_text).style(separator_style)
            out.append(sep_line)
    return out


def _render_aligned_field(
    out: List[HyperlinkLine],
    header: TableCell,
    value: TableCell,
    label_width: int,
    available_width: Optional[int],
    label_style: Style,
) -> None:
    value_indent = FIELD_LEADING_PADDING + label_width + FIELD_GAP
    if available_width is not None:
        value_width = max(available_width - value_indent, MIN_VALUE_WIDTH)
    else:
        value_width = max(_cell_width(value), MIN_VALUE_WIDTH)

    wrapped_value = _wrap_cell(value, value_width)
    for line_index, value_line in enumerate(wrapped_value):
        prefix_parts: List[str] = []
        if line_index == 0:
            label_text = header.plain_text()
            label_pad = " " * FIELD_LEADING_PADDING
            label_display = _display_width(label_text)
            label_styled = (
                HyperlinkLine.new(label_text).style(label_style).line
            )
            extra_pad = " " * (label_width - label_display + FIELD_GAP)
            prefix_parts.append(label_pad)
            prefix_parts.append(label_styled)
            prefix_parts.append(extra_pad)
        else:
            prefix_parts.append(" " * value_indent)
        _push_prefixed_value_line(out, prefix_parts, value_line)


def _render_stacked_field(
    out: List[HyperlinkLine],
    header: TableCell,
    value: TableCell,
    available_width: Optional[int],
    label_style: Style,
) -> None:
    if available_width is not None:
        label_width = max(available_width - FIELD_LEADING_PADDING, 1)
    else:
        label_width = max(_display_width(header.plain_text()), 1)

    label_text = header.plain_text()
    label_styled = HyperlinkLine.new(label_text).style(label_style).line
    label_line = HyperlinkLine.new(
        " " * FIELD_LEADING_PADDING + label_styled
    )
    wrapped_labels = word_wrap_line(label_line.line, RtOptions(width=label_width))
    wrapped_labels = [line_to_static(w) for w in wrapped_labels]
    out.extend(HyperlinkLine.new(line) for line in wrapped_labels)

    if available_width is not None:
        value_width = max(available_width - STACKED_VALUE_INDENT, 1)
    else:
        value_width = max(_cell_width(value), 1)
    for value_line in _wrap_cell(value, value_width):
        _push_prefixed_value_line(
            out,
            [" " * STACKED_VALUE_INDENT],
            value_line,
        )


def _push_prefixed_value_line(
    out: List[HyperlinkLine],
    prefix: List[str],
    value_line: HyperlinkLine,
) -> None:
    """Port of ``codex::markdown_render::table_key_value::push_prefixed_value_line``."""
    shift = sum(_display_width(p) for p in prefix)
    joined_prefix = "".join(prefix)
    new_line_text = joined_prefix + value_line.line
    output_line = HyperlinkLine.new(new_line_text)
    output_line.hyperlinks = [
        _shifted_link(link, shift) for link in value_line.hyperlinks
    ]
    out.append(output_line)


def _shifted_link(link, shift: int):
    from ..terminal_hyperlinks import TerminalHyperlink
    return TerminalHyperlink(
        columns=(link.columns[0] + shift, link.columns[1] + shift),
        destination=link.destination,
    )


def _wrap_cell(cell: TableCell, width: int) -> List[HyperlinkLine]:
    """Port of ``codex::markdown_render::table_key_value::wrap_cell``."""
    if not cell.lines:
        return [HyperlinkLine.new("")]

    wrapped: List[HyperlinkLine] = []
    for source_line in cell.lines:
        source = HyperlinkLine.new(source_line)
        rendered = word_wrap_line(source_line, RtOptions(width=max(1, width)))
        rendered = [line_to_static(r) for r in rendered]
        if not rendered:
            wrapped.append(HyperlinkLine.new(""))
        else:
            wrapped.extend(remap_wrapped_line(source, rendered))
    if not wrapped:
        wrapped.append(HyperlinkLine.new(""))
    return wrapped


def _cell_width(cell: TableCell) -> int:
    """Port of ``codex::markdown_render::table_key_value::cell_width``."""
    if not cell.lines:
        return 0
    return max(_display_width(line) for line in cell.lines)


def _widest_line_width(lines: List[HyperlinkLine]) -> int:
    """Port of ``codex::markdown_render::table_key_value::widest_line_width``."""
    if not lines:
        return 0
    return max(_display_width(line.line) for line in lines)


def _display_width(text: str) -> int:
    """Strip ANSI then count visible characters (Python analogue of UnicodeWidthStr::width)."""
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", text))


__all__ = [
    "should_render_records",
    "render_records",
    "FIELD_LEADING_PADDING",
    "FIELD_GAP",
    "MIN_VALUE_WIDTH",
    "MIN_ALIGNED_COMPACT_VALUE_WIDTH",
    "MIN_ALIGNED_EXPANSIVE_VALUE_WIDTH",
    "MIN_SCANNABLE_NARRATIVE_WIDTH",
    "MIN_SCANNABLE_TOKEN_HEAVY_WIDTH",
    "CRAMPED_EXPANSIVE_CELL_LINES",
    "CATASTROPHIC_NARRATIVE_CELL_LINES",
    "STACKED_VALUE_INDENT",
]
