"""Literal HTML, breaks, and partial table events must preserve visible content."""
import re

from factory.core.tui.markdown_render import (
    HyperlinkLine,
    MarkdownEvent,
    MarkdownEventKind,
    Writer,
)

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def visible(writer):
    return [ANSI.sub("", line.line) for line in writer.output_lines]


def event(kind, text=""):
    return MarkdownEvent(kind=kind, text=text)


def test_block_html_is_literal_and_separated_from_following_text():
    writer = Writer([
        event(MarkdownEventKind.HTML, "<section>alpha\nbeta</section>"),
        event(MarkdownEventKind.TEXT, "after"),
    ], 80, None)
    writer.run()
    assert visible(writer) == ["<section>alpha", "beta</section>", "after"]


def test_inline_html_and_text_share_a_line_without_executing_markup():
    writer = Writer([
        event(MarkdownEventKind.TEXT, "before"),
        event(MarkdownEventKind.INLINE_HTML, "<b>middle</b>"),
        event(MarkdownEventKind.TEXT, "after"),
    ], 80, None)
    writer.run()
    assert visible(writer) == ["before<b>middle</b>after"]


def test_successive_block_html_events_do_not_merge_lines():
    writer = Writer([
        event(MarkdownEventKind.HTML, "<a>one</a>"),
        event(MarkdownEventKind.HTML, "<b>two</b>"),
    ], 80, None)
    writer.run()
    assert visible(writer) == ["<a>one</a>", "<b>two</b>"]


def test_html_and_explicit_breaks_preserve_multiline_table_cell():
    writer = Writer([], 80, None)
    writer.start_table(["left"])
    writer.start_table_cell()
    writer.html("<a>one\ntwo</a>", inline=False)
    writer.text("three")
    writer.hard_break()
    writer.code("four")
    writer.soft_break()
    writer.text("five")
    assert ANSI.sub("", "\n".join(writer.table_state.current_cell.lines)) == (
        "<a>one\ntwo</a>\nthree\nfour five"
    )
    writer.end_table_row()
    writer.end_table()
    output = "\n".join(visible(writer))
    assert all(token in output for token in ("one", "two", "three", "four", "five"))


def test_local_link_label_cannot_leak_html_or_breaks_into_a_cell(tmp_path):
    writer = Writer([], 80, tmp_path)
    writer.start_table(["left"])
    writer.start_table_cell()
    writer.push_link(str(tmp_path / "file.txt"))
    before = list(writer.table_state.current_cell.lines)
    writer.html("<b>hidden-label</b>", inline=False)
    writer.hard_break()
    writer.soft_break()
    writer.code("hidden-code")
    assert writer.table_state.current_cell.lines == before
    writer.pop_link()
    writer.text("next")
    writer.end_table_row()
    writer.end_table()
    output = "\n".join(visible(writer))
    assert "file.txt" in output and "next" in output
    assert "hidden" not in output


def test_explicit_line_breaks_outside_tables_keep_text_order():
    writer = Writer([
        event(MarkdownEventKind.TEXT, "first"),
        event(MarkdownEventKind.HARD_BREAK),
        event(MarkdownEventKind.TEXT, "second"),
        event(MarkdownEventKind.SOFT_BREAK),
        event(MarkdownEventKind.TEXT, "third"),
    ], 80, None)
    writer.run()
    assert visible(writer) == ["first", "second", "third"]


def test_finishing_header_commits_unclosed_cell_before_body():
    writer = Writer([], 80, None)
    writer.start_table(["left", "right"])
    writer.start_table_head()
    writer.start_table_cell()
    writer.text("Name")
    writer.end_table_cell()
    writer.start_table_cell()
    writer.text("Count")
    writer.end_table_head()
    assert writer.table_state.current_cell is None
    assert [cell.plain_text() for cell in writer.table_state.header] == ["Name", "Count"]
    writer.start_table_cell()
    writer.text("Item")
    writer.end_table_cell()
    writer.start_table_cell()
    writer.text("17")
    writer.end_table_row()
    writer.end_table()
    output = "\n".join(visible(writer))
    assert all(token in output for token in ("Name", "Count", "Item", "17"))
    assert output.index("Name") < output.index("Item")


def test_table_end_without_table_does_not_discard_existing_paragraph():
    writer = Writer([], 80, None)
    writer.text("preserved")
    writer.end_table_head()
    writer.end_table_cell()
    writer.end_table_row()
    writer.end_table()
    writer.text(" tail")
    assert visible(writer) == ["preserved tail"]


def test_header_only_table_preserves_labels_and_alignment_markers():
    writer = Writer([], 8, None)
    writer.start_table(["left", "right"])
    writer.start_table_head()
    writer.start_table_cell()
    writer.text("Name")
    writer.end_table_cell()
    writer.start_table_cell()
    writer.text("Count")
    writer.end_table_head()
    writer.end_table()
    output = "".join(visible(writer))
    assert "Name" in output and "Count" in output
    assert ":───" in output and "───:" in output


def test_finishing_header_recovers_cell_when_row_was_not_opened():
    writer = Writer([], 80, None)
    writer.start_table(["left"])
    writer.start_table_cell()
    writer.text("Recovered header")
    writer.end_table_head()
    assert [cell.plain_text() for cell in writer.table_state.header] == ["Recovered header"]
    writer.end_table()
    assert "Recovered header" in "\n".join(visible(writer))


def test_appended_hyperlink_lines_respect_pending_block_separator():
    writer = Writer([], 80, None)
    writer.push_hyperlink_line(HyperlinkLine.new("prefix"))
    writer.push_hyperlink_line(HyperlinkLine.new(" suffix"))
    writer.needs_newline = True
    writer.push_hyperlink_line(HyperlinkLine.new("next"))
    assert visible(writer) == ["prefix suffix", "next"]


def test_empty_inline_html_does_not_erase_surrounding_table_text():
    writer = Writer([], 80, None)
    writer.start_table(["left"])
    writer.start_table_cell()
    writer.text("before")
    writer.html("", inline=True)
    writer.text("after")
    assert ANSI.sub("", writer.table_state.current_cell.plain_text()) == "beforeafter"
