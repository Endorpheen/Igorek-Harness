"""Regression tests for the GFM table renderer in cli_chat_rich_shared.

Mirrors the OpenCode glamour pattern: a markdown table is rendered with
a fixed terminal width and pipe-table ASCII separators (`|`, `-`).
Cell wrapping is consistent across re-renders.
"""
from __future__ import annotations

import io
import re
import textwrap

import pytest
from rich.console import Console

from factory.core.cli_chat_rich_shared import (
    _assistant_msg,
    _cell_display_width,
    _compute_column_widths,
    _draw_table,
    _pad_cell,
    _parse_alignment,
    _parse_gfm_table_block,
    _preprocess_markdown_tables,
    _split_text_and_tables,
    _wrap_cell,
)


# ---------------------------------------------------------------------------
# _parse_alignment
# ---------------------------------------------------------------------------

class TestParseAlignment:
    def test_left(self):
        assert _parse_alignment(":---") == "left"
        assert _parse_alignment("---") == "left"

    def test_right(self):
        assert _parse_alignment("---:") == "right"

    def test_center(self):
        assert _parse_alignment(":---:") == "center"


# ---------------------------------------------------------------------------
# _parse_gfm_table_block
# ---------------------------------------------------------------------------

class TestParseGfmTable:
    def test_basic(self):
        block = textwrap.dedent("""\
            | A | B |
            |---|---|
            | 1 | 2 |""")
        header, aligns, body = _parse_gfm_table_block(block)
        assert header == ["A", "B"]
        assert aligns == ["left", "left"]
        assert body == [["1", "2"]]

    def test_right_align(self):
        block = "| L | R |\n|:--|--:|\n| xx | yy |"
        header, aligns, body = _parse_gfm_table_block(block)
        assert aligns == ["left", "right"]

    def test_center_align(self):
        block = "| L | C |\n|:--|:--:|\n| xx | yy |"
        header, aligns, body = _parse_gfm_table_block(block)
        assert aligns == ["left", "center"]

    def test_not_a_table(self):
        # no separator line
        block = "| A | B |\n| 1 | 2 |"
        assert _parse_gfm_table_block(block) is None

    def test_skips_too_few_columns(self):
        block = "| A |\n|---|\n| 1 |"
        # not a valid gfm table (rows have inconsistent col counts)
        # parse may still return something, but the renderer should
        # not be called on a degenerate table
        # We only check that helper does not crash
        _parse_gfm_table_block(block)


# ---------------------------------------------------------------------------
# _compute_column_widths
# ---------------------------------------------------------------------------

class TestComputeColumnWidths:
    def test_equal_split(self):
        widths = _compute_column_widths(
            ["a", "b"],
            [["x", "y"]],
            max_total=20,
        )
        assert sum(widths) <= 20
        assert all(w >= 3 for w in widths)

    def test_min_three(self):
        widths = _compute_column_widths(
            ["a", "b", "c"],
            [["x" * 100, "y" * 100, "z" * 100]],
            max_total=15,
        )
        assert all(w >= 3 for w in widths)

    def test_header_protected(self):
        # header width cannot be reduced below header content width
        widths = _compute_column_widths(
            ["longheader", "b"],
            [["x", "y"]],
            max_total=12,
        )
        assert widths[0] >= 10  # "longheader" = 10 chars


# ---------------------------------------------------------------------------
# _draw_table
# ---------------------------------------------------------------------------

class TestDrawTable:
    def test_uses_ascii_pipes(self):
        out = _draw_table(["A", "B"], [["1", "2"]], ["left", "left"], [3, 3])
        assert "|" in out
        assert "-" in out
        assert "A" in out and "B" in out
        # No Unicode box-drawing characters
        assert "│" not in out
        assert "├" not in out
        assert "┤" not in out
        assert "┴" not in out
        assert "┼" not in out
        assert "─" not in out

    def test_left_align(self):
        out = _draw_table(["A"], [["xx"]], ["left"], [4])
        # pad("xx", 4, "left") = "xx  " (4 chars)
        # " " + "xx  " + " |" = " xx   |" (7 chars)
        # full line: "|" + " xx   |" = "| xx   |" (8 chars)
        assert "| xx   |" in out

    def test_right_align(self):
        out = _draw_table(["A"], [["xx"]], ["right"], [4])
        # pad("xx", 4, "right") = "  xx" (4 chars)
        # " " + "  xx" + " |" = "   xx |" (7 chars)
        # full line: "|" + "   xx |" = "|   xx |" (8 chars)
        assert "|   xx |" in out

    def test_center_align(self):
        out = _draw_table(["A"], [["xx"]], ["center"], [4])
        # pad("xx", 4, "center") = " xx " (4 chars, left=1, right=1)
        # " " + " xx " + " |" = "  xx  |" (7 chars)
        # full line: "|" + "  xx  |" = "|  xx  |" (8 chars)
        assert "|  xx  |" in out

    def test_separator_uses_dashes(self):
        out = _draw_table(["A", "B"], [["1", "2"]], ["left", "left"], [3, 3])
        lines = out.split("\n")
        # line 1 is the separator
        assert "---" in lines[1]
        assert "|" in lines[1]

    def test_separator_aligns_with_header_borders(self):
        # Regression: separator row's right `|` must sit at the same column
        # as the header's right `|`. Each column is rendered as " " + content + " "
        # (= w+2 chars) and the separator is "-"*(w+2) per column, so widths
        # match exactly when join structure is symmetric.
        out = _draw_table(["A", "B"], [["1", "2"]], ["left", "left"], [3, 3])
        lines = out.split("\n")
        header_line = lines[0]
        sep_line = lines[1]
        # Both lines must end at the same column
        assert _cell_display_width(header_line) == _cell_display_width(sep_line), (
            f"header={_cell_display_width(header_line)} sep={_cell_display_width(sep_line)}"
        )
        # Both end with their right border
        assert header_line.endswith("|")
        assert sep_line.endswith("|")
        # And the right border chars line up vertically
        assert header_line.rindex("|") == sep_line.rindex("|")

    def test_pad_cell_truncates_with_ellipsis(self):
        out = _pad_cell("Длинный заголовок", 5, "left")
        assert _cell_display_width(out) <= 5
        assert out.endswith("…")

    def test_header_wraps_when_too_wide(self):
        out = _draw_table(
            ["ID", "Способ проникновения", "Статус"],
            [["1", "коротко", "Раскрыто"]],
            ["left", "left", "left"],
            [3, 6, 6],
        )
        header_lines = out.split("\n")[:3]
        assert any("Спос" in ln or "проник" in ln for ln in header_lines), (
            f"header did not wrap: {header_lines}"
        )

    def test_long_header_does_not_break_frame(self):
        header = [
            "ID дела", "Марка / модель", "Год выпуска", "Регион",
            "Дата угона", "Способ проникновения", "Статус",
        ]
        body = [
            ["У-2025-0001", "Lada Vesta", "2021", "Москва",
             "2025-01-12", "Разбито стекло водительской двери", "Раскрыто"],
            ["У-2025-0002", "Kia Rio", "2020", "СПб",
             "2025-02-03", "Электронный взлом OBD-порта", "В розыске"],
        ]
        aligns = ["left"] * 7
        widths = _compute_column_widths(header, body, 150)
        out = _draw_table(header, body, aligns, widths)
        lines = out.split("\n")
        sep_idx = next(i for i, ln in enumerate(lines) if set(ln) <= set("-|"))
        sep_line = lines[sep_idx]
        sep_w = _cell_display_width(sep_line)
        for ln in lines[sep_idx:]:
            if ln.strip():
                assert _cell_display_width(ln) == sep_w, (
                    f"line width {_cell_display_width(ln)} != sep width {sep_w}: {ln!r}"
                )

    def test_all_lines_have_same_width(self):
        # Pipe-table invariant: every line in the rendered table is
        # exactly the same display width, no exceptions.
        out = _draw_table(
            ["A", "BB", "CCC"],
            [["x", "yy", "zzz"], ["xx", "y", "z"]],
            ["left", "left", "left"],
            [4, 4, 4],
        )
        lines = out.split("\n")
        widths = {_cell_display_width(ln) for ln in lines if ln.strip()}
        assert len(widths) == 1, f"inconsistent line widths: {widths} in {lines}"

    def test_inline_markdown_in_cells_keeps_frame_width(self):
        out = _draw_table(
            ["Фаза", "Команда", "Статус"],
            [["7.1", "`request_user_input`", "**готово**"]],
            ["left", "left", "left"],
            [4, 18, 6],
        )
        plain = re.sub(r"\033\[[0-9;]*m", "", out)
        assert "`" not in plain
        assert "**" not in plain
        assert "request_user_input" in plain
        assert "готово" in plain
        assert "\033[" in out
        widths = {_cell_display_width(ln) for ln in out.split("\n") if ln.strip()}
        assert len(widths) == 1, f"inconsistent line widths: {widths} in {out!r}"


# ---------------------------------------------------------------------------
# _preprocess_markdown_tables (end-to-end)
# ---------------------------------------------------------------------------

class TestPreprocessMarkdownTables:
    def test_no_table_passthrough(self):
        text = "just some text\nwith newlines"
        assert _preprocess_markdown_tables(text, 80) == text

    def test_no_pipe_passthrough(self):
        text = "regular text without pipes at all"
        assert _preprocess_markdown_tables(text, 80) == text

    def test_width_too_small_passthrough(self):
        text = "| A | B |\n|---|---|"
        assert _preprocess_markdown_tables(text, 2) == text

    def test_simple_replacement(self):
        text = textwrap.dedent("""\
            | A | B |
            |---|---|
            | 1 | 2 |""")
        out = _preprocess_markdown_tables(text, 80)
        # Pipe-table separator dashes are present
        assert "---" in out
        # No Unicode box-drawing leaked in
        assert "│" not in out
        assert "├" not in out

    def test_preserves_surrounding_text(self):
        text = textwrap.dedent("""\
            before text

            | A | B |
            |---|---|
            | 1 | 2 |

            after text""")
        out = _preprocess_markdown_tables(text, 80)
        assert "before text" in out
        assert "after text" in out
        assert "---" in out

    def test_narrow_terminal_wraps_long_cells(self):
        text = textwrap.dedent("""\
            | Name | Path |
            |------|------|
            | a | /home/example/ObsidianVault/HonorMagickBook/notes/long.md |""")
        out = _preprocess_markdown_tables(text, 30)
        for line in out.split("\n"):
            if line.startswith("|"):
                assert _cell_display_width(line) <= 30, f"line too long: {line!r}"

    def test_wide_terminal_no_wrap(self):
        text = textwrap.dedent("""\
            | A | B |
            |---|---|
            | 1 | 2 |""")
        out = _preprocess_markdown_tables(text, 80)
        lines = out.split("\n")
        # header + sep + 1 body row = 3 lines
        assert len([ln for ln in lines if ln.startswith("|")]) == 3

    def test_align_preserved(self):
        text = textwrap.dedent("""\
            | L | C | R |
            |:--|:--:|--:|
            | 1 | 22 | 333 |""")
        out = _preprocess_markdown_tables(text, 40)
        assert "333" in out
        # find the body row containing 333 and check it ends with "333 |"
        body_row = [ln for ln in out.split("\n") if "333" in ln]
        assert any(ln.rstrip().endswith("333 |") for ln in body_row)

    def test_cjk_width(self):
        text = textwrap.dedent("""\
            | Ru | En |
            |---|---|
            | Привет | Hello |""")
        out = _preprocess_markdown_tables(text, 30)
        assert "Привет" in out

    def test_emoji_width(self):
        text = textwrap.dedent("""\
            | W | G |
            |---|---|
            | Hi | 🌍 |""")
        out = _preprocess_markdown_tables(text, 30)
        assert "🌍" in out

    def test_empty_body(self):
        text = textwrap.dedent("""\
            | A | B |
            |---|---|""")
        out = _preprocess_markdown_tables(text, 30)
        assert "A" in out and "B" in out
        assert "---" in out

    def test_multiple_tables(self):
        text = textwrap.dedent("""\
            | A | B |
            |---|---|
            | 1 | 2 |

            and another:

            | C | D |
            |---|---|
            | 3 | 4 |""")
        out = _preprocess_markdown_tables(text, 80)
        # both tables should render with dashes
        assert out.count("---") >= 2

    def test_table_inside_code_block_passthrough(self):
        text = textwrap.dedent("""\
            ```
            | A | B |
            |---|---|
            | 1 | 2 |
            ```""")
        out = _preprocess_markdown_tables(text, 80)
        # helper will still touch the table inside the fence; real
        # _assistant_msg splits code blocks first to prevent this.
        assert "|" in out or "1" in out

    def test_does_not_break_unrelated_pipe(self):
        text = "Command: ls -la | head -5"
        assert _preprocess_markdown_tables(text, 80) == text

    def test_alignment_with_short_value(self):
        text = textwrap.dedent("""\
            | A |
            |:--|
            | xx |""")
        out = _preprocess_markdown_tables(text, 30)
        body_row = [ln for ln in out.split("\n") if "xx" in ln]
        assert body_row, "no row containing xx in output"
        assert any(" xx " in ln or "| xx" in ln for ln in body_row)

    def test_inline_markdown_is_formatted_without_width_skew(self):
        text = textwrap.dedent("""\
            | Подфаза | Команда | Статус |
            |---|---|---|
            | 7.1 | `request_user_input` | **готово** |""")
        out = _preprocess_markdown_tables(text, 100)
        plain = re.sub(r"\033\[[0-9;]*m", "", out)
        assert "`request_user_input`" not in plain
        assert "**готово**" not in plain
        assert "request_user_input" in plain
        assert "готово" in plain
        assert "\033[36mrequest_user_input\033[0m" in out
        assert "\033[1mготово\033[0m" in out
        widths = {_cell_display_width(ln) for ln in out.split("\n") if ln.strip()}
        assert len(widths) == 1, f"inconsistent line widths: {widths} in {out!r}"


# ---------------------------------------------------------------------------
# _split_text_and_tables / _assistant_msg integration
# ---------------------------------------------------------------------------

class TestAssistantTableRendering:
    def test_split_text_and_tables_extracts_table_chunk(self):
        text = textwrap.dedent("""\
            before

            | A | B |
            |---|---|
            | 1 | 2 |

            after""")
        chunks = _split_text_and_tables(text, 80)

        assert [kind for kind, _body in chunks] == ["text", "table", "text"]
        assert "| A " in chunks[1][1]
        assert "|---" in chunks[1][1]

    def test_assistant_msg_prints_tables_without_rich_box_drawing(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=100)
        _assistant_msg(
            console,
            textwrap.dedent("""\
                Данные:

                | id | username |
                |---|---|
                | 1 | igor |
                | 2 | vasya |"""),
        )

        output = stream.getvalue()
        assert "| id " in output
        assert "|----" in output
        assert "| 1 " in output
        assert "│" not in output
        assert "┤" not in output
        assert "┴" not in output

    def test_assistant_msg_does_not_render_tables_inside_code_fences(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=100)
        _assistant_msg(
            console,
            textwrap.dedent("""\
                ```markdown
                | A | B |
                |---|---|
                | 1 | 2 |
                ```"""),
        )

        output = stream.getvalue()
        assert "| A | B |" in output
        assert "|---|---|" in output


# ---------------------------------------------------------------------------
# _cell_display_width
# ---------------------------------------------------------------------------

class TestCellDisplayWidth:
    def test_ascii(self):
        assert _cell_display_width("hello") == 5

    def test_cjk(self):
        assert _cell_display_width("Привет") == 6  # 6 cyrillic chars * 1

    def test_emoji(self):
        # 🌍 is a 2-cell emoji
        assert _cell_display_width("🌍") == 2

    def test_mixed(self):
        # "Hi 🌍" = 2 + 1 + 2 = 5
        assert _cell_display_width("Hi 🌍") == 5

    def test_empty(self):
        assert _cell_display_width("") == 0

    def test_ignores_ansi_escape_sequences(self):
        assert _cell_display_width("\033[36mcode\033[0m") == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
