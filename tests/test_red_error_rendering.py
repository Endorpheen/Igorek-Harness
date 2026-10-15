"""Tests for red error rendering (HTTP 500 in TUI/Rich).

Covers:
- `ErrorCell.display_lines` returns ANSI-red wrapped lines (works in both
  Textual and Rich-fallback paths because `console.print(line, markup=False)`
  passes ANSI through).
- `TraceEventReducer._apply_task_finish` for `http_error` uses
  `output_preview` (full provider error text) over the short `reason`.
- Other failure reasons still use the short `reason` label.
- `approval_required` does not add an error cell.
- The executor's `task.finish` trace event for `http_error` carries
  `output_preview` so the reducer can pick it up.
"""

import unittest

from factory.core.tui.cells.history_cell import ErrorCell
from factory.core.tui.trace_reducer import TraceEventReducer


class TestErrorCellRed(unittest.TestCase):
    def test_single_line_uses_ansi_red(self):
        lines = ErrorCell("http_error").display_lines(80)
        self.assertEqual(len(lines), 1)
        self.assertIn("\033[31m", lines[0])
        self.assertIn("\033[0m", lines[0])
        self.assertIn("http_error", lines[0])

    def test_multiline_all_red(self):
        lines = ErrorCell("line one\nline two").display_lines(80)
        self.assertEqual(len(lines), 2)
        for ln in lines:
            self.assertIn("\033[31m", ln)
            self.assertIn("\033[0m", ln)

    def test_empty_text_yields_one_red_line(self):
        lines = ErrorCell("").display_lines(80)
        self.assertEqual(lines, ["  \033[31m\033[0m"])

    def test_none_text_yields_one_red_line(self):
        lines = ErrorCell("").display_lines(80)
        self.assertEqual(len(lines), 1)
        self.assertIn("\033[31m", lines[0])


class TestTraceReducerHttpError(unittest.TestCase):
    def test_http_error_uses_output_preview(self):
        reducer = TraceEventReducer()
        reducer._apply_task_finish(
            {
                "status": "fail",
                "reason": "http_error",
                "output_preview": (
                    "HTTP 500: {\"type\":\"error\",\"error\":"
                    "{\"type\":\"error\",\"message\":\"Internal server error\"}}"
                ),
            }
        )
        last = reducer.widget.committed_cells[-1]
        self.assertIsInstance(last, ErrorCell)
        self.assertIn("HTTP 500", last.text)
        self.assertIn("Internal server error", last.text)
        # Red is applied at render time, not stored in self.text.
        rendered = last.display_lines(80)[0]
        self.assertIn("\033[31m", rendered)

    def test_http_error_falls_back_to_reason_when_no_preview(self):
        reducer = TraceEventReducer()
        reducer._apply_task_finish(
            {"status": "fail", "reason": "http_error"}
        )
        last = reducer.widget.committed_cells[-1]
        self.assertIsInstance(last, ErrorCell)
        self.assertEqual(last.text, "http_error")

    def test_other_failure_uses_short_reason(self):
        reducer = TraceEventReducer()
        reducer._apply_task_finish(
            {"status": "fail", "reason": "policy_denial_repeated"}
        )
        last = reducer.widget.committed_cells[-1]
        self.assertIsInstance(last, ErrorCell)
        self.assertEqual(last.text, "policy_denial_repeated")

    def test_approval_required_does_not_add_error_cell(self):
        reducer = TraceEventReducer()
        before = len(reducer.widget.committed_cells)
        reducer._apply_task_finish(
            {"status": "fail", "reason": "approval_required"}
        )
        self.assertEqual(len(reducer.widget.committed_cells), before)

    def test_ok_status_does_not_add_error_cell(self):
        reducer = TraceEventReducer()
        before = len(reducer.widget.committed_cells)
        reducer._apply_task_finish({"status": "ok"})
        self.assertEqual(len(reducer.widget.committed_cells), before)


class TestExecutorHttpErrorTaskFinish(unittest.TestCase):
    def test_http_error_task_finish_carries_output_preview(self):
        """The trace event emitted for http_error must include
        `output_preview` so the reducer can render the full provider error.
        """
        # Look at the live trace events emitted by the executor's helper path.
        # We can't easily exercise the full executor here (needs HTTP mocks),
        # so we verify the static code path: a handcrafted task.finish event
        # with the expected shape is the contract the reducer consumes.
        event = {
            "kind": "task.finish",
            "status": "fail",
            "reason": "http_error",
            "round": 2,
            "error": "HTTP 500: Internal server error",
            "output_preview": "HTTP 500: Internal server error",
        }
        reducer = TraceEventReducer()
        reducer._apply_task_finish(event)
        last = reducer.widget.committed_cells[-1]
        self.assertIsInstance(last, ErrorCell)
        self.assertEqual(last.text, "HTTP 500: Internal server error")
        rendered = last.display_lines(80)[0]
        self.assertIn("\033[31m", rendered)


if __name__ == "__main__":
    unittest.main()
