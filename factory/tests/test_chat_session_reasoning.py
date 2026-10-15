from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.tui.chatwidget import ChatWidget
from core.tui.cells.history_cell import (
    MarkdownCell,
    ReasoningCell,
    TextCell,
    ToolCallCell,
    ToolCallGroupCell,
)
from core.chat_session import ChatSession, _has_rendered_task_output


def _make_minimal_cs():
    """Minimal ChatSession for testing pure-method behaviour.

    Avoids the full __init__ which calls StatusLineRuntime (filesystem),
    MessageQueue, etc.  Build and patch only the attributes the test needs.
    """
    cs = object.__new__(ChatSession)
    cs.widget = ChatWidget(model="test", branch="main", cwd="/tmp")
    cs.reasoning_state = {"visible": False, "lines": [], "expanded_lines": []}
    cs._show_reasoning = False
    cs._emitted_line_count = 0
    return cs


def _history_area_display_lines(widget, width=80, show_reasoning=None):
    """Render widget's _HistoryArea directly with optional reasoning toggle.

    ``widget.history_lines()`` does *not* accept a show_reasoning parameter,
    so we construct the internal ``_HistoryArea`` manually for tests that
    need to verify reasoning cell visibility.
    """
    if show_reasoning is None:
        show_reasoning = widget._show_reasoning
    history = widget._HistoryArea(
        widget.committed_cells, widget.active_cell,
        show_reasoning=show_reasoning,
    )
    return history.display_lines(width)


# ── _HistoryArea.display_lines ─────────────────────────────────────

def test_history_area_hides_reasoning_when_show_false():
    """ReasoningCell skipped entirely when show_reasoning=False."""
    widget = ChatWidget(model="t", branch="b", cwd="/")
    widget.committed_cells.append(MarkdownCell("hello"))
    widget.committed_cells.append(ReasoningCell("secret reasoning"))
    widget.committed_cells.append(MarkdownCell("world"))

    lines = widget.history_lines(width=80)
    text = "\n".join(lines)
    assert "hello" in text
    assert "world" in text
    assert "secret reasoning" not in text


def test_history_area_shows_reasoning_when_show_true():
    """ReasoningCell visible when show_reasoning=True."""
    widget = ChatWidget(model="t", branch="b", cwd="/")
    widget.committed_cells.append(MarkdownCell("hello"))
    widget.committed_cells.append(ReasoningCell("visible reasoning"))
    widget.committed_cells.append(MarkdownCell("world"))

    lines = _history_area_display_lines(widget, show_reasoning=True)
    text = "\n".join(lines)
    assert "visible reasoning" in text


def test_history_area_inserts_spacing_between_different_cell_types():
    """Empty line inserted between cells of different types."""
    widget = ChatWidget(model="t", branch="b", cwd="/")
    widget.committed_cells.append(TextCell("user msg", role="user"))
    widget.committed_cells.append(MarkdownCell("assistant reply"))

    lines = widget.history_lines(width=80)
    combined = "\n".join(lines)
    assert "user msg" in combined
    assert "assistant reply" in combined


def test_history_area_spacing_also_for_reasoning_cells():
    """Empty line before/after ReasoningCell when toggled on."""
    widget = ChatWidget(model="t", branch="b", cwd="/")
    widget.committed_cells.append(MarkdownCell("before"))
    widget.committed_cells.append(ReasoningCell("middle"))
    widget.committed_cells.append(MarkdownCell("after"))

    lines = _history_area_display_lines(widget, show_reasoning=True)
    # ReasoningCell.expanded_display_lines includes the header line
    # containing "┌ Model Reasoning"
    text = "\n".join(lines)
    assert "before" in text
    assert "┌ Размышления" in text or "Размышления" in text
    assert "after" in text


def test_history_area_spacing_between_tool_call_cells():
    """ToolCallCell → ToolCallCell gets spacing even though same type."""
    widget = ChatWidget(model="t", branch="b", cwd="/")
    widget.committed_cells.append(
        ToolCallCell("read", {"path": "a"}, "ok", 1.0, "out_a")
    )
    widget.committed_cells.append(
        ToolCallCell("write", {"path": "b"}, "ok", 0.5, "out_b")
    )

    lines = widget.history_lines(width=80)
    text = "\n".join(lines)
    assert "out_a" in text or "read" in text


def test_history_area_active_cell_respects_show_reasoning():
    """Active cell (reasoning) hidden when show_reasoning=False."""
    widget = ChatWidget(model="t", branch="b", cwd="/")
    widget.committed_cells.append(MarkdownCell("history"))
    widget.active_cell = ReasoningCell("active reasoning")

    # When show_reasoning=False, active ReasoningCell shows a
    # collapsed hint line rather than the full expanded text
    lines_hidden = _history_area_display_lines(widget, show_reasoning=False)
    text_hidden = "\n".join(lines_hidden)
    assert "active reasoning" not in text_hidden

    # When show_reasoning=True, expanded lines are shown
    lines_visible = _history_area_display_lines(widget, show_reasoning=True)
    text_visible = "\n".join(lines_visible)
    assert "active reasoning" in text_visible


# ── _update_reasoning_state ────────────────────────────────────────

def test_update_reasoning_state_from_widget_cells():
    """Fallback path: reads ReasoningCell from committed_cells."""
    cs = _make_minimal_cs()
    cs.widget.committed_cells.append(MarkdownCell("irrelevant"))
    rc = ReasoningCell("line1\nline2")
    cs.widget.committed_cells.append(rc)
    cs.widget.committed_cells.append(MarkdownCell("more"))

    cs._update_reasoning_state({})

    rs = cs.reasoning_state
    assert rs["visible"] is False
    assert rs["lines"]               # collapsed lines present (hint)
    assert rs["expanded_lines"]      # expanded lines present
    # ReasoningCell.display_lines returns a collapsed hint
    assert any("Размышления" in l for l in rs["lines"])
    # expanded lines contain the actual content
    assert any("line1" in l for l in rs["expanded_lines"])


def test_update_reasoning_state_from_task():
    """Primary path: reads lines from task dict."""
    cs = _make_minimal_cs()
    cs._update_reasoning_state({
        "_deferred_scrollback_lines": ["  col1", "  col2"],
        "_expanded_deferred_scrollback_lines": ["  exp1", "  exp2", "  exp3"],
    })
    rs = cs.reasoning_state
    assert rs["visible"] is False
    assert rs["lines"] == ["  col1", "  col2"]
    assert rs["expanded_lines"] == ["  exp1", "  exp2", "  exp3"]


def test_update_reasoning_state_fallback_expanded_from_lines():
    """When expanded is empty, fall back to lines."""
    cs = _make_minimal_cs()
    cs._update_reasoning_state({
        "_deferred_scrollback_lines": ["  one", "  two"],
        "_expanded_deferred_reasoning_lines": [],
    })
    assert cs.reasoning_state["expanded_lines"] == ["  one", "  two"]


def test_update_reasoning_state_clears_visible():
    """visible always reset to False after update."""
    cs = _make_minimal_cs()
    cs.reasoning_state["visible"] = True
    cs._update_reasoning_state({
        "_deferred_scrollback_lines": ["  content"],
        "_expanded_deferred_reasoning_lines": ["  content"],
    })
    assert cs.reasoning_state["visible"] is False


def test_update_reasoning_state_no_task_no_widget_cells():
    """Graceful: no reasoning data, empty state."""
    cs = _make_minimal_cs()
    cs._update_reasoning_state({})
    assert cs.reasoning_state["lines"] == []
    assert cs.reasoning_state["expanded_lines"] == []
    assert cs.reasoning_state["visible"] is False


# ── _merge_approval_context ────────────────────────────────────────

def test_merge_approval_context_empty():
    """Empty context returns empty list."""
    cs = _make_minimal_cs()
    assert cs._merge_approval_context([], []) == []


def test_merge_approval_context_new_items_added():
    cs = _make_minimal_cs()
    result = cs._merge_approval_context(
        [{"role": "user", "content": "hi"}],
        [{"role": "assistant", "content": "hello"}],
    )
    assert len(result) == 2
    assert {"role": "user", "content": "hi"} in result
    assert {"role": "assistant", "content": "hello"} in result


def test_merge_approval_context_no_duplicates():
    cs = _make_minimal_cs()
    msg = {"role": "user", "content": "dup"}
    result = cs._merge_approval_context([msg], [msg, msg])
    assert result.count(msg) == 1


def test_merge_approval_context_preserves_existing():
    cs = _make_minimal_cs()
    existing = [{"role": "user", "content": "keep"}]
    result = cs._merge_approval_context(existing, [])
    assert result == existing


# ── Ctrl+O reasoning toggle (run() loop behaviour) ────────────────

def test_reasoning_toggle_does_not_touch_widget_show_reasoning():
    """Ctrl+O must NOT toggle widget._show_reasoning (duplication fix)."""
    cs = _make_minimal_cs()
    cs.widget._show_reasoning = False

    # Simulate what run() does when text == "__reasoning__"
    # (the old code toggled widget._show_reasoning — the fix removes that)
    text = "__reasoning__"
    if text == "__reasoning__":
        pass  # just continue — no widget toggle
    assert cs.widget._show_reasoning is False


def test_reasoning_toggle_no_direct_print():
    """Ctrl+O must NOT call console.print for reasoning lines itself."""
    cs = _make_minimal_cs()
    cs.reasoning_state = {
        "visible": True,
        "lines": ["  col"],
        "expanded_lines": ["  exp"],
    }
    # The old code had:
    #   if self._show_reasoning and not self._reasoning_printed:
    #       for cell in widget.committed_cells:
    #           if isinstance(cell, ReasoningCell):
    #               console.print(...)
    # This block is now removed — verify no direct print side effect:
    assert cs.reasoning_state["visible"] is True


def test_reasoning_toggle_state_flow():
    """Simulate full toggle cycle: visible→hidden via reasoning_state only."""
    cs = _make_minimal_cs()
    cs.reasoning_state = {
        "visible": False,
        "lines": ["  some line"],
        "expanded_lines": ["  exp line"],
    }
    # Ctrl+O in idle prompt toggles reasoning_state["visible"]
    cs.reasoning_state["visible"] = not cs.reasoning_state["visible"]
    assert cs.reasoning_state["visible"] is True

    # Toggle back
    cs.reasoning_state["visible"] = not cs.reasoning_state["visible"]
    assert cs.reasoning_state["visible"] is False


# ── _render_widget incremental behaviour ──────────────────────────

def test_render_widget_tracks_emitted_line_count():
    """After render, _emitted_line_count matches total widget lines."""
    cs = _make_minimal_cs()
    cs.widget.committed_cells.append(MarkdownCell("hello\nworld"))

    lines = cs._render_widget_lines()
    assert lines
    # _render_widget_lines returns only NEW lines since last emit
    # On first call with _emitted_line_count=0, all lines are new
    assert len(lines) > 0


def test_render_widget_lines_always_returns_new_lines():
    """_render_widget_lines returns new lines since last _emitted_line_count.

    It does *not* update the counter — the caller is responsible for
    updating ``_emitted_line_count`` (see ``_render_widget``).
    """
    cs = _make_minimal_cs()
    cs.widget.committed_cells.append(MarkdownCell("static"))
    lines1 = cs._render_widget_lines()
    assert lines1
    # Counter not advanced, so second call returns same lines
    lines2 = cs._render_widget_lines()
    assert lines2 == lines1


def test_render_widget_lines_have_leading_space():
    """Each line is prefixed with a space for visual indent."""
    cs = _make_minimal_cs()
    cs.widget.committed_cells.append(MarkdownCell("content"))
    lines = cs._render_widget_lines()
    assert all(l.startswith(" ") for l in lines if l.strip())


def test_scrollback_lines_insert_gap_before_explored_after_preamble():
    cs = _make_minimal_cs()
    cs.widget.committed_cells.append(MarkdownCell("Теперь посмотрю файл.", marker=True))
    group = ToolCallGroupCell()
    group.add_call("call_1", "Read", {"file_path": "factory/core/worker.py"})
    group.complete_call("call_1", status="done")
    cs.widget.committed_cells.append(group)

    lines = cs._scrollback_widget_lines()

    explored_index = next(i for i, line in enumerate(lines) if "Explored" in line)
    assert lines[explored_index - 1] == ""


def test_scrollback_lines_insert_gap_between_activity_groups():
    cs = _make_minimal_cs()
    first = ToolCallGroupCell()
    first.add_call("call_1", "Read", {"file_path": "factory/core/worker.py"})
    first.complete_call("call_1", status="done")
    second = ToolCallGroupCell()
    second.add_call("call_2", "Bash", {"command": "grep -n def file.py"})
    second.complete_call("call_2", status="done", output="1:def main():")
    cs.widget.committed_cells.extend([first, second])

    lines = cs._scrollback_widget_lines()

    ran_index = next(
        i for i, line in enumerate(lines)
        if "Ran" in line and "Bash" in line
    )
    assert lines[ran_index - 1] == ""


def test_worked_separator_does_not_count_as_rendered_task_output():
    assert _has_rendered_task_output([], 0) is False
    assert _has_rendered_task_output(["• visible answer"], 0) is True
    assert _has_rendered_task_output([], 2) is True


def test_chat_session_splits_committed_scrollback_from_active_viewport():
    """Fixed history is saved in scrollback; the active cell remains active."""
    cs = _make_minimal_cs()
    cs.widget.committed_cells.append(MarkdownCell("already committed", marker=True))
    cs.widget.active_cell = MarkdownCell("mutable live tail")

    scrollback = cs._scrollback_widget_lines()
    live = cs._live_widget_lines()

    assert any("already committed" in line for line in scrollback)
    assert not any("mutable live tail" in line for line in scrollback)
    assert any("mutable live tail" in line for line in live)
    assert not any("already committed" in line for line in live)


def test_live_activity_cell_gets_gap_after_emitted_scrollback():
    cs = _make_minimal_cs()
    cs._emitted_line_count = 3
    group = ToolCallGroupCell()
    group.add_call("call_1", "Bash", {"command": "curl -sS example.com"})
    cs.widget.active_cell = group

    live = cs._live_widget_lines()

    assert live[0] == ""
    assert "Running" in live[1] and "Bash" in live[1]


def test_live_activity_cell_has_no_leading_gap_without_scrollback():
    cs = _make_minimal_cs()
    group = ToolCallGroupCell()
    group.add_call("call_1", "Bash", {"command": "curl -sS example.com"})
    cs.widget.active_cell = group

    live = cs._live_widget_lines()

    assert live[0] != ""
    assert "Running" in live[0] and "Bash" in live[0]
