import re
import tempfile
import unittest
from pathlib import Path

from factory.core.trace_taxonomy import CcTraceEvent
from factory.core.tui.cells.history_cell import (
    DiffCell,
    ErrorCell,
    MarkdownCell,
    SeparatorCell,
    PlanCell,
    ReasoningCell,
    TextCell,
    ToolCallCell,
    ToolCallGroupCell,
)
from factory.core.tui.trace_reducer import (
    TraceEventReducer,
    _find_matching_edit_preview,
    _resolve_edit_start_line,
)


class TraceEventReducerTest(unittest.TestCase):
    def test_plan_updated_renders_structured_cell_without_generic_tool_cell(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [{"id": "plan-1", "name": "update_plan", "input": {"plan": []}}],
        })
        reducer.apply({
            "kind": CcTraceEvent.PLAN_UPDATED,
            "explanation": "Начинаю работу.",
            "plan": [
                {"step": "Inspect", "status": "completed"},
                {"step": "Implement", "status": "in_progress"},
            ],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "plan-1",
            "tool_name": "update_plan",
            "raw_result_preview": "Plan updated",
        })

        self.assertIsNone(reducer.widget.active_cell)
        self.assertEqual(len(reducer.widget.committed_cells), 1)
        self.assertIsInstance(reducer.widget.committed_cells[0], PlanCell)
        lines = [re.sub(r"\033\[[0-9;]*m", "", line) for line in reducer.display_lines()]
        self.assertEqual(lines, [
            "• Updated Plan",
            "  └ Начинаю работу.",
            "    ✔ Inspect",
            "    □ Implement",
        ])

    def test_tool_request_creates_running_cells(self):
        reducer = TraceEventReducer()

        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [
                {"id": "a", "name": "Bash", "input": {"command": "pwd"}},
                {"id": "b", "name": "Read", "input": {"file_path": "x.py"}},
            ],
        })

        self.assertEqual(reducer.widget.committed_cells, [])
        self.assertIsInstance(reducer.widget.active_cell, ToolCallGroupCell)
        self.assertEqual(len(reducer.widget.active_cell.records), 2)
        self.assertTrue(reducer.widget.active_cell.is_active())

    def test_tool_result_replaces_matching_running_cell_by_id(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [
                {"id": "a", "name": "Bash", "input": {"command": "pwd"}},
                {"id": "b", "name": "Read", "input": {"file_path": "x.py"}},
            ],
        })

        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "raw_result_preview": "/home/example",
            "duration_ms": 120,
        })

        self.assertEqual(reducer.widget.committed_cells, [])
        group = reducer.widget.active_cell
        self.assertIsInstance(group, ToolCallGroupCell)
        self.assertFalse(group.records[0].is_running)
        self.assertEqual(group.records[0].status, "done")
        self.assertEqual(group.records[0].elapsed, 0.12)
        self.assertTrue(group.records[1].is_running)

    def test_tool_group_flushes_when_all_calls_complete(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [
                {"id": "a", "name": "Bash", "input": {"command": "pwd"}},
            ],
        })

        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "raw_result_preview": "/home/example",
            "duration_ms": 120,
        })

        self.assertIsNone(reducer.widget.active_cell)
        self.assertIsInstance(reducer.widget.committed_cells[0], ToolCallGroupCell)

    def test_read_only_batch_renders_as_compact_exploring_cell(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [
                {"id": "a", "name": "Grep", "input": {"pattern": "TODO", "path": "src/"}},
                {"id": "b", "name": "Read", "input": {"file_path": "src/app.py"}},
                {"id": "c", "name": "Glob", "input": {"pattern": "**/*.py"}},
            ],
        })

        lines = [re.sub(r"\033\[[0-9;]*m", "", line) for line in reducer.widget.active_cell.display_lines(80)]

        self.assertEqual(lines, [
            "• Exploring",
            "  └ Search TODO src/",
            "    Read src/app.py",
            "    List **/*.py",
        ])
        self.assertFalse(any("Running" in line for line in lines))

    def test_mixed_batch_keeps_read_only_segments_compact(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [
                {"id": "a", "name": "Read", "input": {"file_path": "handoff.md"}},
                {"id": "b", "name": "Read", "input": {"file_path": "Ledger.md"}},
                {"id": "c", "name": "Bash", "input": {"command": "ls -la"}},
            ],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "tool_name": "Read",
            "raw_result_preview": "handoff",
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "b",
            "tool_name": "Read",
            "raw_result_preview": "ledger",
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "c",
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "raw_result_preview": "files",
        })

        lines = [re.sub(r"\033\[[0-9;]*m", "", line) for line in reducer.display_lines()]

        self.assertIn("• Explored", lines)
        self.assertIn("  └ Read handoff.md", lines)
        self.assertIn("    Read Ledger.md", lines)
        self.assertTrue(any("Ran Bash" in line for line in lines))
        self.assertFalse(any("Ran Read" in line for line in lines))

    def test_failed_read_in_mixed_batch_remains_expanded(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [
                {"id": "a", "name": "Read", "input": {"file_path": "ok.py"}},
                {"id": "b", "name": "Read", "input": {"file_path": "missing.py"}},
                {"id": "c", "name": "Bash", "input": {"command": "pwd"}},
            ],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "tool_name": "Read",
            "raw_result_preview": "ok",
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "b",
            "tool_name": "Read",
            "raw_result_preview": "Error executing Read: missing.py",
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "c",
            "tool_name": "Bash",
            "tool_input": {"command": "pwd"},
            "raw_result_preview": "/tmp",
        })

        lines = [re.sub(r"\033\[[0-9;]*m", "", line) for line in reducer.display_lines()]

        self.assertIn("• Explored", lines)
        self.assertIn("  └ Read ok.py", lines)
        self.assertTrue(any("Ran Read" in line for line in lines))
        self.assertTrue(any("Error executing Read: missing.py" in line for line in lines))

    def test_sequential_read_only_batches_coalesce_until_task_finish(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [{"id": "a", "name": "Read", "input": {"file_path": "a.py"}}],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "tool_name": "Read",
            "raw_result_preview": "a",
        })

        self.assertEqual(reducer.widget.committed_cells, [])
        self.assertIsInstance(reducer.widget.active_cell, ToolCallGroupCell)
        self.assertFalse(reducer.widget.active_cell.is_active())

        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 2,
            "tools": [{"id": "b", "name": "Grep", "input": {"pattern": "needle"}}],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "b",
            "tool_name": "Grep",
            "raw_result_preview": "a.py:1:needle",
        })
        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "",
        })

        self.assertIsNone(reducer.widget.active_cell)
        self.assertEqual(len(reducer.widget.committed_cells), 1)
        lines = [
            re.sub(r"\033\[[0-9;]*m", "", line)
            for line in reducer.widget.committed_cells[0].display_lines(80)
        ]
        self.assertEqual(lines, [
            "• Explored",
            "  └ Read a.py",
            "    Search needle",
        ])

    def test_non_exploration_batch_flushes_completed_explored_group(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [{"id": "a", "name": "Read", "input": {"file_path": "a.py"}}],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "tool_name": "Read",
            "raw_result_preview": "a",
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 2,
            "tools": [{"id": "b", "name": "Bash", "input": {"command": "pwd"}}],
        })

        self.assertEqual(len(reducer.widget.committed_cells), 1)
        explored = reducer.widget.committed_cells[0]
        self.assertIsInstance(explored, ToolCallGroupCell)
        self.assertTrue(any("Explored" in line for line in explored.display_lines(80)))
        self.assertIsInstance(reducer.widget.active_cell, ToolCallGroupCell)
        self.assertTrue(any("Running" in line for line in reducer.widget.active_cell.display_lines(80)))

    def test_streaming_text_flushes_completed_explored_group_before_answer(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [{"id": "a", "name": "Read", "input": {"file_path": "a.py"}}],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "tool_name": "Read",
            "raw_result_preview": "a",
        })
        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "Готово"})

        self.assertEqual(len(reducer.widget.committed_cells), 3)
        self.assertIsInstance(reducer.widget.committed_cells[0], ToolCallGroupCell)
        self.assertTrue(any(
            "Explored" in line
            for line in reducer.widget.committed_cells[0].display_lines(80)
        ))
        self.assertIsInstance(reducer.widget.committed_cells[1], SeparatorCell)
        self.assertIsInstance(reducer.widget.committed_cells[2], TextCell)
        self.assertIsInstance(reducer.widget.active_cell, MarkdownCell)

    def test_reasoning_flushes_completed_explored_group_before_reasoning_cell(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [{"id": "a", "name": "Read", "input": {"file_path": "a.py"}}],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "tool_name": "Read",
            "raw_result_preview": "a",
        })
        reducer.apply({"kind": CcTraceEvent.MODEL_REASONING_DELTA, "text": "Думаю."})
        reducer.apply({
            "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
            "text_preview": "Готово.",
            "text_full": "Готово.",
        })

        self.assertIsInstance(reducer.widget.committed_cells[0], ToolCallGroupCell)
        self.assertIsInstance(reducer.widget.committed_cells[1], ReasoningCell)

    def test_streaming_commits_reasoning_before_streamed_answer(self):
        reducer = TraceEventReducer()
        reducer.apply({"kind": CcTraceEvent.MODEL_REASONING_DELTA, "text": "Думаю."})
        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "round": 1, "text": "Привет"})
        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "round": 1, "text": ", мир"})
        reducer.apply({
            "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
            "round": 1,
            "text_preview": "Привет, мир",
            "text_full": "Привет, мир",
        })
        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "Привет, мир",
        })
        kinds = [type(c).__name__ for c in reducer.widget.committed_cells]
        self.assertEqual(kinds, ["ReasoningCell", "MarkdownCell"])

    def test_failed_read_falls_back_to_regular_tool_rendering_with_error(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [{"id": "a", "name": "Read", "input": {"file_path": "missing.py"}}],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "tool_name": "Read",
            "raw_result_preview": "Error executing Read: missing.py",
        })

        self.assertIsNone(reducer.widget.active_cell)
        self.assertEqual(len(reducer.widget.committed_cells), 1)
        lines = [
            re.sub(r"\033\[[0-9;]*m", "", line)
            for line in reducer.widget.committed_cells[0].display_lines(80)
        ]
        self.assertTrue(any("Ran Read" in line for line in lines))
        self.assertTrue(any("Error executing Read: missing.py" in line for line in lines))
        self.assertFalse(any("Explored" in line for line in lines))

    def test_failed_tool_result_marks_error(self):
        reducer = TraceEventReducer()

        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "missing",
            "tool_name": "Bash",
            "tool_input": {"command": "bad"},
            "raw_result_preview": "Error executing Bash: boom",
            "duration_ms": 20,
        })

        cell = reducer.widget.committed_cells[0]
        self.assertIsInstance(cell, ToolCallCell)
        self.assertEqual(cell.status, "error")

    def test_task_finish_adds_final_markdown(self):
        reducer = TraceEventReducer()

        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "**готово**",
        })

        self.assertIsInstance(reducer.widget.committed_cells[0], MarkdownCell)
        self.assertTrue(any("готово" in line for line in reducer.display_lines()))

    def test_preamble_before_tool_batch_is_inserted_once(self):
        reducer = TraceEventReducer()

        reducer.apply({
            "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
            "text_preview": "Сначала проверю файлы.",
        })
        self.assertFalse(reducer.consume_dirty())

        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [
                {"id": "a", "name": "Bash", "input": {"command": "ls"}},
            ],
        })

        self.assertIsInstance(reducer.widget.committed_cells[0], MarkdownCell)
        self.assertTrue(any("Сначала проверю файлы" in line for line in reducer.display_lines()))
        self.assertIsInstance(reducer.widget.active_cell, ToolCallGroupCell)

    def test_preamble_after_work_gets_full_width_separator_and_blank_line(self):
        reducer = TraceEventReducer()

        # First round: preamble + tool (no prior work → no separator)
        reducer.apply({
            "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
            "text_preview": "Сначала проверю файлы.",
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [{"id": "a", "name": "Bash", "input": {"command": "ls"}}],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "tool_name": "Bash",
            "raw_result_preview": "a.py b.py",
            "duration_ms": 1,
        })

        # Second round: preamble after work → Codex-like separator + blank line
        reducer.apply({
            "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
            "text_preview": "Теперь прочитаю.",
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 2,
            "tools": [{"id": "b", "name": "Read", "input": {"file_path": "a.py"}}],
        })

        cells = reducer.widget.committed_cells
        self.assertIsInstance(cells[0], MarkdownCell)
        self.assertIsInstance(cells[1], ToolCallGroupCell)
        self.assertIsInstance(cells[2], SeparatorCell)
        self.assertIsInstance(cells[3], TextCell)
        self.assertIsInstance(cells[4], MarkdownCell)
        self.assertTrue(any("Теперь прочитаю" in line for line in reducer.display_lines()))

        # Separator must span almost the whole terminal width while leaving
        # enough columns free that Rich's ``cell_len`` (which does not subtract
        # the embedded ``\033[2m``/``\033[0m`` escapes) does not wrap the line
        # onto a second physical row. Empirically the wrap threshold for a dim
        # line printed via ``console.print(..., markup=False)`` is
        # ``width - 6`` visible columns, so the separator reserves 6 columns.
        sep_lines = cells[2].display_lines(80)
        self.assertEqual(len(sep_lines), 1)
        self.assertEqual(len(re.sub(r"\033\[[0-9;]*m", "", sep_lines[0])), 74)

    def test_task_finish_does_not_duplicate_same_preamble_text(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
            "text_preview": "Готово.",
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [
                {"id": "a", "name": "Bash", "input": {"command": "true"}},
            ],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "raw_result_preview": "",
            "duration_ms": 1,
        })

        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "Готово.",
        })

        markdown_cells = [
            cell for cell in reducer.widget.committed_cells
            if isinstance(cell, MarkdownCell)
        ]
        self.assertEqual(len(markdown_cells), 1)

    def test_non_streaming_task_finish_renders_full_text_once(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
            "text_preview": "Полный ответ модели обрезан...",
            "text_full": "Полный ответ модели не должен обрезаться.",
        })

        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "Полный ответ модели обрезан...",
        })

        markdown_cells = [
            cell for cell in reducer.widget.committed_cells
            if isinstance(cell, MarkdownCell)
        ]
        self.assertEqual(len(markdown_cells), 1)
        self.assertTrue(any(
            "Полный ответ модели не должен обрезаться." in line
            for line in reducer.display_lines()
        ))

    def test_non_streaming_numeric_answer_with_dot_is_visible(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
            "text_preview": "8.",
            "text_full": "8.",
        })

        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "8.",
        })

        plain = [
            re.sub(r"\033\[[0-9;]*[A-Za-z]", "", line)
            for line in reducer.display_lines()
        ]
        self.assertTrue(any("8." in line for line in plain))

    def test_task_finish_after_tool_adds_separator_before_answer(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [{"id": "a", "name": "Bash", "input": {"command": "true"}}],
        })
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "a",
            "raw_result_preview": "",
            "duration_ms": 1,
        })

        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "Готово.",
        })

        self.assertTrue(any(isinstance(cell, SeparatorCell) for cell in reducer.widget.committed_cells))
        markdown_cells = [cell for cell in reducer.widget.committed_cells if isinstance(cell, MarkdownCell)]
        self.assertTrue(markdown_cells[-1].marker)

    def test_approval_required_does_not_add_status_cell(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [
                {"id": "a", "name": "Bash", "input": {"command": "rm x"}},
            ],
        })

        reducer.apply({
            "kind": CcTraceEvent.TOOL_APPROVAL_REQUIRED,
            "tool_use_id": "a",
            "tool": "Bash",
            "target": "rm x",
        })

        self.assertIsInstance(reducer.widget.active_cell, ToolCallGroupCell)
        self.assertTrue(reducer.widget.active_cell.is_active())
        # Approval required is now a no-op — no StatusCell added
        self.assertFalse(any("approval required" in line for line in reducer.display_lines()))

    def test_approval_required_task_finish_does_not_render_failure_reason(self):
        reducer = TraceEventReducer()
        reducer.apply_many([
            {
                "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                "round": 1,
                "tools": [
                    {"id": "a", "name": "Write", "input": {"file_path": "chart.py"}},
                ],
            },
            {
                "kind": CcTraceEvent.TOOL_APPROVAL_REQUIRED,
                "tool_use_id": "a",
                "tool": "Write",
                "target": "chart.py",
            },
            {
                "kind": CcTraceEvent.TASK_FINISH,
                "status": "fail",
                "reason": "approval_required",
                "rounds": 1,
                "tool_calls_count": 1,
            },
        ])

        lines = [re.sub(r"\033\[[0-9;]*m", "", line) for line in reducer.display_lines()]
        self.assertTrue(any("Running Write" in line for line in lines))
        self.assertFalse(any("approval required" in line.lower() for line in lines))
        self.assertFalse(any("approval_required" in line for line in lines))
        self.assertFalse(any(isinstance(cell, ErrorCell) for cell in reducer.widget.committed_cells))

    def test_dirty_flag_is_consumed(self):
        reducer = TraceEventReducer()

        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "done",
        })

        self.assertTrue(reducer.consume_dirty())
        self.assertFalse(reducer.consume_dirty())

    def test_failed_task_finish_adds_error_cell(self):
        reducer = TraceEventReducer()

        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "fail",
            "reason": "max_rounds",
        })

        self.assertIsInstance(reducer.widget.committed_cells[0], ErrorCell)
        self.assertTrue(any("max_rounds" in line for line in reducer.display_lines()))

    def test_text_delta_creates_streaming_markdown(self):
        reducer = TraceEventReducer()

        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "Hello "})
        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "world\n"})
        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "Next line"})

        self.assertTrue(reducer.dirty)
        self.assertTrue(reducer._is_streaming)
        # Completed lines become stable history; only the unfinished tail stays active.
        self.assertIsInstance(reducer.widget.active_cell, MarkdownCell)
        self.assertEqual(len(reducer.widget.committed_cells), 1)
        self.assertEqual(reducer.widget.committed_cells[0].text, "Hello world")
        self.assertEqual(reducer.widget.active_cell.text, "Next line")
        # Full accumulated text
        self.assertEqual(reducer._stream_text, "Hello world\nNext line")

    def test_streaming_round_response_does_not_replace_live_tail(self):
        reducer = TraceEventReducer()
        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "stable\ntail"})
        active = reducer.widget.active_cell

        reducer.apply({
            "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
            "text_preview": "stable\ntail",
            "text_full": "stable\ntail",
        })

        self.assertIs(reducer.widget.active_cell, active)
        self.assertEqual(reducer.widget.active_cell.text, "tail")

    def test_streaming_preserves_completed_blank_line(self):
        reducer = TraceEventReducer()

        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "paragraph\n\n"})

        lines = reducer.widget.committed_cells[0].display_lines(80)
        self.assertEqual(lines, ["• paragraph"])

    def test_text_delta_finalized_on_task_finish(self):
        reducer = TraceEventReducer()

        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "Streaming text\n"})
        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "",
        })

        self.assertFalse(reducer._is_streaming)
        self.assertIsNone(reducer.widget.active_cell)

    def test_task_finish_markdown_table_uses_ascii_pipe_renderer(self):
        reducer = TraceEventReducer()

        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "| A | B |\n|---|---|\n| 1 | 2 |",
        })

        joined = "\n".join(reducer.display_lines(80))
        self.assertIn("A", joined)
        markdown_cells = [
            c for c in reducer.widget.committed_cells
            if isinstance(c, MarkdownCell)
        ]
        self.assertEqual(len(markdown_cells), 1)

    def test_text_delta_no_duplicate_on_task_finish(self):
        """Streaming text should NOT be duplicated by task.finish output_preview."""
        reducer = TraceEventReducer()

        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "Hello world"})
        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "Hello world",
        })

        markdown_cells = [
            c for c in reducer.widget.committed_cells
            if isinstance(c, MarkdownCell)
        ]
        # Only ONE markdown cell — no duplicate from task.finish
        self.assertEqual(len(markdown_cells), 1)

    def test_text_delta_finalized_before_tool_request_batch(self):
        reducer = TraceEventReducer()

        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "Let me check\n"})
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [{"id": "a", "name": "Bash", "input": {"command": "ls"}}],
        })

        self.assertFalse(reducer._is_streaming)
        # Streaming markdown flushed before tool group
        markdown_cells = [
            c for c in reducer.widget.committed_cells
            if isinstance(c, MarkdownCell)
        ]
        self.assertGreaterEqual(len(markdown_cells), 1)
        self.assertIsInstance(reducer.widget.active_cell, ToolCallGroupCell)

    def test_reasoning_delta_accumulates(self):
        reducer = TraceEventReducer()

        reducer.apply({"kind": CcTraceEvent.MODEL_REASONING_DELTA, "text": "Hmm..."})
        self.assertTrue(reducer.dirty)
        self.assertEqual(reducer.widget.reasoning_buffer, "Hmm...")

    def test_round_response_flushes_reasoning_as_styled_history_before_answer(self):
        reducer = TraceEventReducer()
        reducer.apply({"kind": CcTraceEvent.MODEL_REASONING_DELTA, "text": "Проверяю детали."})

        reducer.apply({
            "kind": CcTraceEvent.MODEL_ROUND_RESPONSE,
            "text_preview": "Готово.",
            "text_full": "Готово.",
        })
        reducer.apply({
            "kind": CcTraceEvent.TASK_FINISH,
            "status": "ok",
            "output_preview": "Готово.",
        })

        self.assertIsInstance(reducer.widget.committed_cells[0], ReasoningCell)
        lines = reducer.widget.committed_cells[0].display_lines(80)
        self.assertTrue(any("Размышления модели" in line for line in lines))
        self.assertFalse(any("Проверяю детали." in line for line in lines))
        expanded_lines = reducer.widget.committed_cells[0].expanded_display_lines(80)
        self.assertTrue(any("Проверяю детали." in line for line in expanded_lines))
        self.assertFalse(any("<think>" in line for line in reducer.display_lines()))

    def test_edit_result_adds_tool_cell_without_diff_cell(self):
        reducer = TraceEventReducer()

        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "missing",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "test.py",
                "old_string": "print('hello')",
                "new_string": "print('world')",
            },
            "raw_result_preview": "File edited successfully: test.py",
            "duration_ms": 50,
        })

        cells = reducer.widget.committed_cells
        # Edit result renders only status/output; diff is request-time preview.
        self.assertEqual(len(cells), 1)
        self.assertIsInstance(cells[0], ToolCallCell)
        self.assertFalse(any(isinstance(c, DiffCell) for c in cells))

    def test_write_result_no_diff_cell(self):
        reducer = TraceEventReducer()

        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "missing",
            "tool_name": "Write",
            "tool_input": {
                "file_path": "new.py",
                "content": "print('hello')",
            },
            "raw_result_preview": "File written successfully: new.py",
            "duration_ms": 20,
        })

        cells = reducer.widget.committed_cells
        # Only ToolCallCell, no DiffCell
        self.assertEqual(len(cells), 1)
        self.assertIsInstance(cells[0], ToolCallCell)

    def test_bash_result_no_diff_cell(self):
        reducer = TraceEventReducer()

        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "missing",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "raw_result_preview": "file1.py\nfile2.py",
            "duration_ms": 100,
        })

        cells = reducer.widget.committed_cells
        self.assertEqual(len(cells), 1)
        self.assertIsInstance(cells[0], ToolCallCell)

    def test_edit_in_group_adds_diff_after_flush(self):
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
            "round": 1,
            "tools": [{"id": "e1", "name": "Edit", "input": {
                "file_path": "a.py", "old_string": "x=1", "new_string": "x=2",
            }}],
        })
        # DiffCell preview is shown immediately at request time
        diff_cells_before = [c for c in reducer.widget.committed_cells if isinstance(c, DiffCell)]
        self.assertEqual(len(diff_cells_before), 1)

        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "e1",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "a.py", "old_string": "x=1", "new_string": "x=2",
            },
            "raw_result_preview": "File edited successfully: a.py",
            "duration_ms": 30,
        })

        # Group flushed, DiffCell NOT duplicated
        cells = reducer.widget.committed_cells
        diff_cells = [c for c in cells if isinstance(c, DiffCell)]
        self.assertEqual(len(diff_cells), 1)

    def test_edit_result_only_does_not_add_diff(self):
        """Edit results render tool status only; diffs are request-time previews."""
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "standalone1",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "b.py", "old_string": "a", "new_string": "b",
            },
            "raw_result_preview": "File edited successfully: b.py",
            "duration_ms": 10,
        })
        cells = reducer.widget.committed_cells
        diff_cells = [c for c in cells if isinstance(c, DiffCell)]
        self.assertEqual(len(diff_cells), 0)
        self.assertEqual(len(cells), 1)
        self.assertIsInstance(cells[0], ToolCallCell)

    def test_resolve_edit_start_line_returns_first_match_in_real_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.py"
            lines = ["x = 1\n" for _ in range(457)] + ["x = 99\n", "x = 3\n"]
            path.write_text("".join(lines), encoding="utf-8")

            self.assertEqual(_resolve_edit_start_line(str(path), "x = 99\n"), 458)

    def test_resolve_edit_start_line_returns_none_when_old_string_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.py"
            path.write_text("hello world\n", encoding="utf-8")

            self.assertIsNone(_resolve_edit_start_line(str(path), "missing block"))

    def test_resolve_edit_start_line_returns_none_when_file_missing(self):
        self.assertIsNone(_resolve_edit_start_line("/nonexistent/path/to/x.py", "anything"))

    def test_resolve_edit_start_line_returns_none_for_empty_inputs(self):
        self.assertIsNone(_resolve_edit_start_line("", "x"))
        self.assertIsNone(_resolve_edit_start_line("/tmp/x", ""))

    def test_edit_diff_cell_resolves_start_line_from_real_file(self):
        """Real Edit flow: DiffCell.start_line is set to the file line of old_string."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.py"
            body = "x = 1\n" * 457 + "old = 2\n" + "y = 3\n"
            path.write_text(body, encoding="utf-8")

            reducer = TraceEventReducer()
            reducer.apply({
                "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                "round": 1,
                "tools": [{
                    "id": "e1",
                    "name": "Edit",
                    "input": {
                        "file_path": str(path),
                        "old_string": "old = 2\n",
                        "new_string": "new = 2\n",
                    },
                }],
            })

            diff_cells = [c for c in reducer.widget.committed_cells if isinstance(c, DiffCell)]
            self.assertEqual(len(diff_cells), 1)
            self.assertEqual(diff_cells[0].start_line, 458)

    def test_edit_diff_cell_start_line_none_when_old_string_not_found_in_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.py"
            path.write_text("completely different content\n", encoding="utf-8")

            reducer = TraceEventReducer()
            reducer.apply({
                "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                "round": 1,
                "tools": [{
                    "id": "e1",
                    "name": "Edit",
                    "input": {
                        "file_path": str(path),
                        "old_string": "ghost = 1\n",
                        "new_string": "ghost = 2\n",
                    },
                }],
            })

            diff_cells = [c for c in reducer.widget.committed_cells if isinstance(c, DiffCell)]
            self.assertEqual(len(diff_cells), 1)
            self.assertIsNone(diff_cells[0].start_line)

    def test_edit_diff_cell_start_line_uses_first_match_for_replace_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.py"
            path.write_text("foo\n" * 100 + "TARGET\n" + "bar\n" + "TARGET\n", encoding="utf-8")

            reducer = TraceEventReducer()
            reducer.apply({
                "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                "round": 1,
                "tools": [{
                    "id": "ra1",
                    "name": "Edit",
                    "input": {
                        "file_path": str(path),
                        "old_string": "TARGET\n",
                        "new_string": "HIT\n",
                        "replace_all": True,
                    },
                }],
            })

            diff_cells = [c for c in reducer.widget.committed_cells if isinstance(c, DiffCell)]
            self.assertEqual(len(diff_cells), 1)
            self.assertEqual(diff_cells[0].start_line, 101)
            self.assertTrue(diff_cells[0].replace_all)

    def test_edit_result_does_not_create_second_diff_when_preview_exists(self):
        """When the Edit preview is already shown in the same reducer, the
        result must not create a duplicate DiffCell.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.py"
            body = "x = 1\n" * 457 + "MARKER = 1\n" + "y = 2\n"
            path.write_text(body, encoding="utf-8")

            reducer = TraceEventReducer()
            reducer.apply({
                "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                "round": 1,
                "tools": [{
                    "id": "e1",
                    "name": "Edit",
                    "input": {
                        "file_path": str(path),
                        "old_string": "MARKER = 1\n",
                        "new_string": "MARKER = 999\n",
                    },
                }],
            })

            # Simulate Edit execution mutating the file.
            path.write_text("x = 1\n" * 457 + "MARKER = 999\n" + "y = 2\n", encoding="utf-8")

            # Result phase with a different tool_use_id (id-space mismatch).
            reducer.apply({
                "kind": CcTraceEvent.TOOL_RESULT,
                "tool_use_id": "different_id",
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(path),
                    "old_string": "MARKER = 1\n",
                    "new_string": "MARKER = 999\n",
                },
                "raw_result_preview": "File edited successfully",
                "duration_ms": 5,
            })

            diff_cells = [c for c in reducer.widget.committed_cells if isinstance(c, DiffCell)]
            # Only the preview DiffCell must exist.
            self.assertEqual(len(diff_cells), 1)
            self.assertEqual(diff_cells[0].start_line, 458)

    def test_edit_result_only_task_does_not_create_fragment_relative_diff(self):
        """Approval continuations can deliver only TOOL_RESULT in a fresh task.
        That path must not render a second fragment-relative diff.
        """
        reducer = TraceEventReducer()
        reducer.apply({
            "kind": CcTraceEvent.TOOL_RESULT,
            "tool_use_id": "approval_continuation",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/tmp/app.py",
                "old_string": "old\n",
                "new_string": "new\n",
            },
            "raw_result_preview": "File edited successfully: /tmp/app.py",
            "duration_ms": 5,
        })

        self.assertFalse(
            any(isinstance(c, DiffCell) for c in reducer.widget.committed_cells)
        )
        self.assertEqual(len(reducer.widget.committed_cells), 1)
        self.assertIsInstance(reducer.widget.committed_cells[0], ToolCallCell)

    def test_edit_approval_continuation_request_does_not_create_second_preview(self):
        """Approval continuations re-emit a TOOL_REQUEST_BATCH with the same
        tool input. After the file has been mutated, _resolve_edit_start_line
        returns None, so without a guard a second preview would render with
        fragment-relative line numbers.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.py"
            body = "x = 1\n" * 6 + "MARKER = 1\n" + "y = 2\n"
            path.write_text(body, encoding="utf-8")

            reducer = TraceEventReducer()
            edit_tool = {
                "id": "e1", "name": "Edit",
                "input": {
                    "file_path": str(path),
                    "old_string": "MARKER = 1\n",
                    "new_string": "MARKER = 999\n",
                },
            }
            # First request: real preview with start_line=7.
            reducer.apply({
                "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                "round": 1,
                "tools": [edit_tool],
            })
            # File mutated before continuation arrives.
            path.write_text("x = 1\n" * 6 + "MARKER = 999\n" + "y = 2\n", encoding="utf-8")
            # Approval continuation: same tool input, fresh batch.
            reducer.apply({
                "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                "round": 0,
                "forced": True,
                "pending_after_approval": True,
                "tools": [edit_tool],
            })

            diff_cells = [c for c in reducer.widget.committed_cells if isinstance(c, DiffCell)]
            self.assertEqual(len(diff_cells), 1)
            self.assertEqual(diff_cells[0].start_line, 7)

    def test_find_matching_edit_preview_returns_none_when_no_match(self):
        reducer = TraceEventReducer()
        reducer.widget.committed_cells.append(DiffCell("a", "b", "x.py"))
        result = _find_matching_edit_preview(
            reducer.widget.committed_cells, "x.py", "z",
        )
        self.assertIsNone(result)

    def test_find_matching_edit_preview_returns_latest_matching_cell(self):
        reducer = TraceEventReducer()
        reducer.widget.committed_cells.append(DiffCell("a", "b", "x.py", start_line=10))
        reducer.widget.committed_cells.append(DiffCell("c", "d", "x.py", start_line=20))
        result = _find_matching_edit_preview(
            reducer.widget.committed_cells, "x.py", "c",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.start_line, 20)


if __name__ == "__main__":
    unittest.main()
