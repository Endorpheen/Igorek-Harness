"""Independent acceptance for the public streamed Markdown compatibility APIs."""

import pytest

from factory.core.tui.chatwidget import ChatWidget
from factory.core.tui.cells.history_cell import MarkdownCell, TextCell, ToolCallCell

PAIRS = [
    ("handle_streaming_delta", "handle_stream_finished"),
    ("handle_agent_message_delta", "handle_agent_message_complete"),
]


@pytest.mark.parametrize("delta,finish", PAIRS)
def test_raw_unicode_crlf_survives_every_split_without_renderer(monkeypatch, delta, finish):
    def forbidden_render(*args):
        raise AssertionError("Raw streaming must not reconstruct rendered lines")

    monkeypatch.setattr(MarkdownCell, "display_lines", forbidden_render)
    source = "  🐙e\u0301\r\n\r\n```py\n    x = '🐸'\n```"
    for cut in range(len(source) + 1):
        widget = ChatWidget(width=9)
        getattr(widget, delta)(source[:cut])
        getattr(widget, delta)(source[cut:])
        getattr(widget, finish)()
        assert widget.stream_buffer == "" and widget.active_cell is None
        assert "".join(cell.text for cell in widget.committed_cells) == source


@pytest.mark.parametrize("delta,finish", PAIRS)
def test_tail_keeps_one_coherent_markdown_card_and_live_prefix(delta, finish):
    widget = ChatWidget()
    widget.set_on_redraw(lambda: redraws.append(True))
    redraws = []
    source = "```python\n    print('🐙')\n```"
    getattr(widget, delta)(source)
    cell = widget.active_cell
    assert isinstance(cell, MarkdownCell)
    assert cell.text == "```python\n    print('🐙')\n"
    assert widget.stream_buffer == "```" and redraws
    getattr(widget, finish)()
    assert widget.committed_cells == [cell]
    assert cell.text == source and widget.active_cell is None
    assert widget.stream_buffer == ""


@pytest.mark.parametrize("delta,finish", PAIRS)
def test_separate_messages_keep_prior_card_identity_and_raw_text(delta, finish):
    widget = ChatWidget()
    sources = ["first\nlast", "next\r\n  🐸", "third\n"]
    previous = []
    for source in sources:
        getattr(widget, delta)(source)
        getattr(widget, finish)()
        assert len(widget.committed_cells) == len(previous) + 1
        assert all(actual is expected for actual, expected in zip(widget.committed_cells, previous))
        previous = list(widget.committed_cells)
        assert [cell.text for cell in previous] == sources[: len(previous)]
        getattr(widget, finish)()
        assert widget.committed_cells == previous


@pytest.mark.parametrize("delta,finish", PAIRS)
@pytest.mark.parametrize("has_newline", [False, True])
def test_real_active_tool_cell_is_preserved_and_never_reused(delta, finish, has_newline):
    widget = ChatWidget()
    prior = TextCell("earlier", role="user")
    tool = ToolCallCell("Read", {"file_path": "fixture.txt"}, output="fixture result")
    widget.committed_cells.append(prior)
    widget.set_active_cell(tool)
    source = "new\nanswer" if has_newline else "answer"
    getattr(widget, delta)(source)
    getattr(widget, finish)()
    assert widget.committed_cells[0] is prior and prior.text == "earlier"
    assert widget.committed_cells[1] is tool
    assert tool.output == "fixture result" and tool.tool_input == {"file_path": "fixture.txt"}
    assert len(widget.committed_cells) == 3 and widget.committed_cells[2].text == source
