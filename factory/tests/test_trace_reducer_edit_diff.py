from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.trace_taxonomy import CcTraceEvent
from core.tui.cells.history_cell import DiffCell, MarkdownCell
from core.tui.trace_reducer import TraceEventReducer


def _edit_request_event() -> dict:
    return {
        "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
        "round": 1,
        "tools": [
            {
                "id": "edit_1",
                "name": "Edit",
                "input": {
                    "file_path": "/tmp/example.py",
                    "old_string": "value = 1\n",
                    "new_string": "value = 2\n",
                },
            }
        ],
    }


def test_edit_diff_preview_is_suppressed_across_approval_reducer_boundary():
    shown_signatures: set[tuple[str, str, str, bool]] = set()

    first = TraceEventReducer(shown_edit_diff_signatures=shown_signatures)
    first.apply(_edit_request_event())

    second = TraceEventReducer(shown_edit_diff_signatures=shown_signatures)
    second.apply(_edit_request_event())

    first_diffs = [c for c in first.widget.committed_cells if isinstance(c, DiffCell)]
    second_diffs = [c for c in second.widget.committed_cells if isinstance(c, DiffCell)]
    assert len(first_diffs) == 1
    assert second_diffs == []


def test_distinct_edit_diff_preview_is_not_suppressed():
    shown_signatures: set[tuple[str, str, str, bool]] = set()

    first = TraceEventReducer(shown_edit_diff_signatures=shown_signatures)
    first.apply(_edit_request_event())

    changed = _edit_request_event()
    changed["tools"][0]["id"] = "edit_2"
    changed["tools"][0]["input"]["new_string"] = "value = 3\n"
    second = TraceEventReducer(shown_edit_diff_signatures=shown_signatures)
    second.apply(changed)

    second_diffs = [c for c in second.widget.committed_cells if isinstance(c, DiffCell)]
    assert len(second_diffs) == 1


def test_model_preamble_markdown_gets_codex_like_marker():
    reducer = TraceEventReducer()
    reducer.apply({
        "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
        "text_full": "Сейчас найду подходящий маленький Python-файл в проекте.",
    })
    reducer.apply({
        "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
        "round": 1,
        "tools": [
            {
                "id": "read_1",
                "name": "Read",
                "input": {"file_path": "/tmp/example.py"},
            }
        ],
    })

    markdown_cells = [
        c for c in reducer.widget.committed_cells if isinstance(c, MarkdownCell)
    ]
    assert markdown_cells
    assert markdown_cells[0].marker is True
    assert markdown_cells[0].display_lines(120)[0].startswith("• Сейчас найду")
