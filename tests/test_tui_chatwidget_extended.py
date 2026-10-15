import re
import unittest
from factory.core.tui.chatwidget import (
    ChatWidget, UserMessage, TokenUsageInfo, PendingSteer,
)
from factory.core.tui.cells.history_cell import (
    TextCell, ToolCallCell, ToolCallGroupCell, MarkdownCell, StatusCell,
    ReasoningCell, ErrorCell, PlanCell,
)

class ChatWidgetExtendedTest(unittest.TestCase):
    def setUp(self):
        self.w = ChatWidget(width=80)

    def test_submit_user_message_idle(self):
        submitted = []
        self.w.set_on_submit_message(lambda t: submitted.append(t))
        msg = UserMessage(text="hello")
        self.w.submit_user_message(msg)
        self.assertTrue(submitted)

    def test_submit_user_message_running(self):
        self.w.agent_turn_running = True
        msg = UserMessage(text="steer this")
        self.w.submit_user_message(msg)
        self.assertEqual(len(self.w.pending_steers), 1)

    def test_queue_user_message(self):
        msg = UserMessage(text="queued")
        self.w.queue_user_message(msg)
        self.assertEqual(len(self.w.queued_user_messages), 1)

    def test_on_task_started(self):
        self.w.on_task_started()
        self.assertTrue(self.w.agent_turn_running)
        self.assertIsNotNone(self.w.status_widget)

    def test_on_task_complete(self):
        self.w.on_task_started()
        self.w.active_cell = MarkdownCell("streaming output")
        self.w.on_task_complete("done")
        self.assertFalse(self.w.agent_turn_running)
        # Active cell should be committed
        self.assertIsNone(self.w.active_cell)

    def test_on_interrupted(self):
        self.w.on_task_started()
        self.w.active_cell = MarkdownCell("partial")
        self.w.on_interrupted()
        self.assertFalse(self.w.agent_turn_running)

    def test_add_text_cell(self):
        self.w.add_text_cell("hi", role="user")
        self.assertEqual(len(self.w.committed_cells), 1)

    def test_add_tool_call_cell(self):
        self.w.add_tool_call_cell("Bash", {"command": "ls"}, "done", 0.5)
        self.assertEqual(len(self.w.committed_cells), 1)

    def test_history_lines_add_blank_line_between_adjacent_tool_groups(self):
        first = ToolCallGroupCell()
        first.add_call("a", "web_search", {"query": "first"})
        first.complete_call("a", status="done")
        second = ToolCallGroupCell()
        second.add_call("b", "web_extract", {"url": "https://example.com"})
        second.complete_call("b", status="done")
        self.w.committed_cells.extend([first, second])

        lines = self.w.history_lines(120)

        second_header = next(i for i, line in enumerate(lines) if "web_extract" in line)
        self.assertEqual("", lines[second_header - 1])
        self.assertNotEqual("", lines[second_header - 2])

    def test_add_error_cell(self):
        self.w.add_error_cell("fail")
        self.assertEqual(len(self.w.committed_cells), 1)

    def test_clear_history(self):
        self.w.add_text_cell("test")
        self.w.clear_history()
        self.assertEqual(len(self.w.committed_cells), 0)

    def test_set_token_info(self):
        info = TokenUsageInfo(context_window=32000, remaining_tokens=16000)
        self.w.set_token_info(info)
        self.assertEqual(self.w.context_window_percent, 50)

    def test_handle_agent_message_delta(self):
        self.w.handle_agent_message_delta("hello\nworld")
        self.assertTrue(self.w.had_work_activity)
        self.assertIsNotNone(self.w.active_cell)

    def test_handle_agent_message_complete(self):
        self.w.stream_buffer = "final text"
        self.w.handle_agent_message_complete()
        self.assertEqual(self.w.stream_buffer, "")

    def test_handle_reasoning_delta(self):
        self.w.handle_reasoning_delta("thinking...")
        self.assertGreater(len(self.w.reasoning_buffer), 0)

    def test_reasoning_cell_defaults_to_collapsed_summary(self):
        lines = ReasoningCell("line one\nline two").display_lines(80)

        self.assertEqual(len(lines), 1)
        self.assertIn("Размышления модели", lines[0])
        self.assertIn("2 строк", lines[0])
        self.assertIn("Ctrl+O", lines[0])

    def test_reasoning_cell_expands_to_dim_labeled_block(self):
        lines = ReasoningCell("line one\nline two").expanded_display_lines(80)

        self.assertIn("Размышления модели", lines[1])
        self.assertTrue(any("│ " in line and "line one" in line for line in lines))
        self.assertTrue(lines[-1].endswith("└\033[0m"))

    def test_handle_tool_call_started(self):
        self.w.handle_tool_call_started("Read", {"file_path": "x.py"})
        self.assertEqual(len(self.w.committed_cells), 1)

    def test_handle_tool_call_completed(self):
        self.w.handle_tool_call_started("Bash", {"command": "ls"})
        self.w.handle_tool_call_completed("Bash", {"command": "ls"}, "done", 0.5)
        self.assertEqual(len(self.w.committed_cells), 1)

    def test_handle_error(self):
        self.w.handle_error("something broke")
        self.assertEqual(len(self.w.committed_cells), 1)

    def test_handle_warning(self):
        self.w.handle_warning("careful")
        self.assertEqual(len(self.w.committed_cells), 1)

    def test_handle_plan_update_appends_codex_style_revisions(self):
        self.w.handle_plan_update([{"step": "step 1", "status": "in_progress"}])
        self.w.handle_plan_update([{"step": "step 1", "status": "completed"}])
        self.assertEqual(len(self.w.committed_cells), 2)
        self.assertTrue(all(isinstance(cell, PlanCell) for cell in self.w.committed_cells))

    def test_plan_cell_renders_codex_statuses(self):
        cell = PlanCell([
            {"step": "Inspect", "status": "completed"},
            {"step": "Implement", "status": "in_progress"},
            {"step": "Verify", "status": "pending"},
        ])
        lines = [re.sub(r"\033\[[0-9;]*m", "", line) for line in cell.display_lines(80)]
        self.assertEqual(lines, [
            "• Updated Plan",
            "  └ ✔ Inspect",
            "    □ Implement",
            "    □ Verify",
        ])

    def test_drain_queued_to_pending(self):
        self.w.queue_user_message(UserMessage(text="q1"))
        self.w.queue_user_message(UserMessage(text="q2"))
        result = self.w.drain_queued_to_pending()
        self.assertEqual(len(result), 2)

    def test_desired_height(self):
        h = self.w.desired_height(80)
        self.assertGreaterEqual(h, 0)

    def test_as_renderable(self):
        flex = self.w.as_renderable()
        self.assertIsNotNone(flex)
        self.assertGreaterEqual(len(flex._children), 1)
