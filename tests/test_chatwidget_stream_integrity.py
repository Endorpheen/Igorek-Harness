"""Stream integrity tests for ChatWidget public streaming methods.

Covers the public streaming pairs:
  - handle_streaming_delta / handle_stream_finished
  - handle_agent_message_delta / handle_agent_message_complete

Original streamed text must survive delta accumulation and finalization
verbatim (raw text, not re-rendered through display_lines).
"""
from __future__ import annotations

import pytest

from factory.core.tui.chatwidget import ChatWidget
from factory.core.tui.cells.history_cell import MarkdownCell, TextCell


def _make_widget() -> ChatWidget:
    return ChatWidget(width=80)


def _committed_markdown_text(widget: ChatWidget) -> str:
    """Concatenation of raw .text of committed MarkdownCells."""
    return "".join(
        cell.text for cell in widget.committed_cells if isinstance(cell, MarkdownCell)
    )


# ─── handle_streaming_delta / handle_stream_finished ─────────────


class TestStreamingPair:
    def test_empty_stream_after_finish(self):
        widget = _make_widget()
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == ""

    def test_single_word_without_newline(self):
        widget = _make_widget()
        widget.handle_streaming_delta("hello")
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "hello"

    def test_string_with_trailing_newline(self):
        widget = _make_widget()
        widget.handle_streaming_delta("first\n")
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "first\n"

    def test_several_lines_with_trailing_newline(self):
        widget = _make_widget()
        widget.handle_streaming_delta("first\nsecond\nlast\n")
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "first\nsecond\nlast\n"

    def test_several_lines_without_trailing_newline(self):
        widget = _make_widget()
        widget.handle_streaming_delta("first\nsecond\nlast")
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "first\nsecond\nlast"

    @pytest.mark.parametrize(
        "source, chunks",
        [
            ("first\nsecond\nlast", ["first\nsecond\nlast"]),
            ("first\nsecond\nlast", ["first\nsecond", "\nlast"]),
            ("first\nsecond\nlast", ["first", "\nsecond", "\nlast"]),
            ("one\ntwo\nthree", ["one\ntwo", "\nthree"]),
            ("hello", ["hello"]),
            ("first\nsecond\nlast\n", ["first\nsecond", "\nlast\n"]),
        ],
        ids=[
            "one-chunk",
            "newline-boundary-split",
            "three-chunk",
            "two-lines-split",
            "single-word",
            "trailing-newline-split",
        ],
    )
    def test_same_text_gives_same_result_regardless_of_chunks(self, source, chunks):
        widget = _make_widget()
        for chunk in chunks:
            widget.handle_streaming_delta(chunk)
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == source

    def test_all_two_chunk_splits_of_short_text(self):
        source = "first\nsecond\nlast"
        for split in range(1, len(source)):
            widget = _make_widget()
            widget.handle_streaming_delta(source[:split])
            widget.handle_streaming_delta(source[split:])
            widget.handle_stream_finished()
            assert widget.stream_buffer == ""
            assert _committed_markdown_text(widget) == source, f"split at {split}"

    def test_empty_lines_in_the_middle_and_consecutive(self):
        widget = _make_widget()
        widget.handle_streaming_delta("x\n\n\ny\n")
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "x\n\n\ny\n"

    def test_indentation_unicode_and_markdown_preserved(self):
        source = "    indented\n\n```python\ncode\n```\n🎉 héllo"
        widget = _make_widget()
        widget.handle_streaming_delta(source)
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == source

    def test_last_incomplete_tail_extends_accumulated_text(self):
        widget = _make_widget()
        widget.handle_streaming_delta("first\nsecond")
        assert widget.stream_buffer == "second"
        assert widget.active_cell is not None
        assert widget.active_cell.text == "first\n"
        widget.handle_streaming_delta("\nlast")
        assert widget.stream_buffer == "last"
        assert widget.active_cell is not None
        assert widget.active_cell.text == "first\nsecond\n"
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "first\nsecond\nlast"

    def test_second_finish_does_not_duplicate_history(self):
        widget = _make_widget()
        widget.handle_streaming_delta("first\nsecond\nlast")
        widget.handle_stream_finished()
        committed_after_first = list(widget.committed_cells)
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert widget.committed_cells == committed_after_first

    def test_committed_history_not_changed_or_repeated(self):
        widget = _make_widget()
        widget.committed_cells.append(MarkdownCell("prev\n"))
        widget.handle_streaming_delta("first\nsecond\nlast")
        widget.handle_stream_finished()
        assert _committed_markdown_text(widget) == "prev\nfirst\nsecond\nlast"

    def test_non_markdown_active_cell_committed_before_new_text(self):
        widget = _make_widget()
        widget.set_active_cell(TextCell("tool-ish text\n", "assistant"))
        widget.handle_streaming_delta("first\nsecond\nlast")
        widget.handle_stream_finished()
        assert widget.stream_buffer == ""
        assert isinstance(widget.committed_cells[0], TextCell)
        assert _committed_markdown_text(widget) == "first\nsecond\nlast"


# ─── handle_agent_message_delta / handle_agent_message_complete ──


class TestAgentMessagePair:
    def test_empty_stream_after_complete(self):
        widget = _make_widget()
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == ""

    def test_single_word_without_newline(self):
        widget = _make_widget()
        widget.handle_agent_message_delta("hello")
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "hello"

    def test_string_with_trailing_newline(self):
        widget = _make_widget()
        widget.handle_agent_message_delta("first\n")
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "first\n"

    def test_several_lines_with_trailing_newline(self):
        widget = _make_widget()
        widget.handle_agent_message_delta("first\nsecond\nlast\n")
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "first\nsecond\nlast\n"

    def test_several_lines_without_trailing_newline(self):
        widget = _make_widget()
        widget.handle_agent_message_delta("first\nsecond\nlast")
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "first\nsecond\nlast"

    @pytest.mark.parametrize(
        "source, chunks",
        [
            ("first\nsecond\nlast", ["first\nsecond\nlast"]),
            ("first\nsecond\nlast", ["first\nsecond", "\nlast"]),
            ("first\nsecond\nlast", ["first", "\nsecond", "\nlast"]),
            ("one\ntwo\nthree", ["one\ntwo", "\nthree"]),
            ("hello", ["hello"]),
            ("first\nsecond\nlast\n", ["first\nsecond", "\nlast\n"]),
        ],
        ids=[
            "one-chunk",
            "newline-boundary-split",
            "three-chunk",
            "two-lines-split",
            "single-word",
            "trailing-newline-split",
        ],
    )
    def test_same_text_gives_same_result_regardless_of_chunks(self, source, chunks):
        widget = _make_widget()
        for chunk in chunks:
            widget.handle_agent_message_delta(chunk)
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == source

    def test_all_two_chunk_splits_of_short_text(self):
        source = "first\nsecond\nlast"
        for split in range(1, len(source)):
            widget = _make_widget()
            widget.handle_agent_message_delta(source[:split])
            widget.handle_agent_message_delta(source[split:])
            widget.handle_agent_message_complete()
            assert widget.stream_buffer == ""
            assert _committed_markdown_text(widget) == source, f"split at {split}"

    def test_last_incomplete_tail_extends_accumulated_text(self):
        widget = _make_widget()
        widget.handle_agent_message_delta("first\nsecond")
        assert widget.stream_buffer == "second"
        assert widget.active_cell is not None
        assert widget.active_cell.text == "first\n"
        widget.handle_agent_message_delta("\nlast")
        assert widget.stream_buffer == "last"
        assert widget.active_cell is not None
        assert widget.active_cell.text == "first\nsecond\n"
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "first\nsecond\nlast"

    def test_empty_lines_in_the_middle_and_consecutive(self):
        widget = _make_widget()
        widget.handle_agent_message_delta("x\n\n\ny\n")
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == "x\n\n\ny\n"

    def test_indentation_unicode_and_markdown_preserved(self):
        source = "    indented\n\n```python\ncode\n```\n🎉 héllo"
        widget = _make_widget()
        widget.handle_agent_message_delta(source)
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert _committed_markdown_text(widget) == source

    def test_second_complete_does_not_duplicate_history(self):
        widget = _make_widget()
        widget.handle_agent_message_delta("first\nsecond\nlast")
        widget.handle_agent_message_complete()
        committed_after_first = list(widget.committed_cells)
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert widget.committed_cells == committed_after_first

    def test_committed_history_not_changed_or_repeated(self):
        widget = _make_widget()
        widget.committed_cells.append(MarkdownCell("prev\n"))
        widget.handle_agent_message_delta("first\nsecond\nlast")
        widget.handle_agent_message_complete()
        assert _committed_markdown_text(widget) == "prev\nfirst\nsecond\nlast"

    def test_non_markdown_active_cell_committed_before_new_text(self):
        widget = _make_widget()
        widget.set_active_cell(TextCell("tool-ish text\n", "assistant"))
        widget.handle_agent_message_delta("first\nsecond\nlast")
        widget.handle_agent_message_complete()
        assert widget.stream_buffer == ""
        assert isinstance(widget.committed_cells[0], TextCell)
        assert _committed_markdown_text(widget) == "first\nsecond\nlast"
