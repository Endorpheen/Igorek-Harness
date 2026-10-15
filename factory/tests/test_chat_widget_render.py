from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.tui.chatwidget import ChatWidget
from core.tui.cells.history_cell import (
    ErrorCell,
    MarkdownCell,
    ReasoningCell,
    StatusCell,
    TextCell,
    ToolCallCell,
)


# ── sanity / structural ───────────────────────────────────────────

def test_widget_starts_empty():
    w = ChatWidget(model="m", branch="b", cwd="/")
    assert w.committed_cells == []
    assert w.active_cell is None


def test_widget_history_lines_no_cells():
    w = ChatWidget(model="m", branch="b", cwd="/")
    assert w.history_lines(width=80) == []


# ── single cell types ─────────────────────────────────────────────

def test_single_text_cell():
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.committed_cells.append(TextCell("hello", role="user"))
    lines = w.history_lines(80)
    text = "\n".join(lines)
    assert "hello" in text


def test_single_markdown_cell():
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.committed_cells.append(MarkdownCell("**bold** text"))
    lines = w.history_lines(80)
    text = "\n".join(lines)
    # markdown rendered (output depends on markdown_render internals)
    assert len(lines) > 0


def test_single_tool_call_cell():
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.committed_cells.append(ToolCallCell("read", {"path": "x"}, "ok", 0.5, "output"))
    lines = w.history_lines(80)
    text = "\n".join(lines)
    assert "output" in text or "read" in text


def test_single_error_cell():
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.committed_cells.append(ErrorCell("boom"))
    lines = w.history_lines(80)
    text = "\n".join(lines)
    assert "boom" in text


def test_single_status_cell():
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.committed_cells.append(StatusCell("  status line"))
    lines = w.history_lines(80)
    text = "\n".join(lines)
    assert "status" in text


# ── multiple cells ────────────────────────────────────────────────

def test_multiple_mixed_cells():
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.committed_cells.append(TextCell("user one", role="user"))
    w.committed_cells.append(MarkdownCell("reply one"))
    w.committed_cells.append(TextCell("user two", role="user"))
    w.committed_cells.append(MarkdownCell("reply two"))

    lines = w.history_lines(80)
    text = "\n".join(lines)
    assert "user one" in text
    assert "reply one" in text
    assert "user two" in text
    assert "reply two" in text


def test_tool_call_pair():
    """Two ToolCallCells in sequence — works without crash."""
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.committed_cells.append(ToolCallCell("read", {}, "ok", 1.0, "a"))
    w.committed_cells.append(ToolCallCell("write", {}, "ok", 0.5, "b"))

    lines = w.history_lines(80)
    assert len(lines) > 0


# ── ReasoningCell show/hide ───────────────────────────────────────

def test_reasoning_hidden_by_default():
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.committed_cells.append(ReasoningCell("secret"))
    lines = w.history_lines(80)
    assert "secret" not in "\n".join(lines)


def test_reasoning_visible_when_toggled():
    """ReasoningCell expanded text visible when _show_reasoning=True.

    Note: ``history_lines()`` always uses show_reasoning=False, so we
    must construct ``_HistoryArea`` manually to test the toggled state.
    """
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.committed_cells.append(ReasoningCell("visible now"))
    # Directly use internal _HistoryArea with show_reasoning=True
    history = w._HistoryArea(w.committed_cells, w.active_cell, show_reasoning=True)
    lines = history.display_lines(80)
    assert "visible now" in "\n".join(lines)


def test_active_reasoning_cell_hidden():
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.active_cell = ReasoningCell("active think")
    lines = w.history_lines(80)
    assert "active think" not in "\n".join(lines)


def test_active_reasoning_cell_visible_when_toggled():
    w = ChatWidget(model="m", branch="b", cwd="/")
    w.active_cell = ReasoningCell("active think")
    # Directly use internal _HistoryArea with show_reasoning=True
    history = w._HistoryArea(w.committed_cells, w.active_cell, show_reasoning=True)
    lines = history.display_lines(80)
    assert "active think" in "\n".join(lines)


# ── render (full) ─────────────────────────────────────────────────

def test_desired_height_grows_with_content():
    w = ChatWidget(model="m", branch="b", cwd="/")
    h0 = w.desired_height(80)
    w.committed_cells.append(MarkdownCell("hello\nworld\nline3"))
    h1 = w.desired_height(80)
    assert h1 >= h0 + 3
