"""Tests for footer.py — port of Codex footer logic."""
import unittest
from factory.core.tui.bottom_pane.footer import (
    FooterMode, FooterProps, SummaryHintKind, LeftSideState,
    CollaborationModeIndicator, SummaryLeft,
    context_window_line, left_side_line, quit_shortcut_reminder_line,
    esc_hint_line, footer_from_props_lines, shows_passive_footer_line,
    passive_footer_status_line, can_show_left_with_context,
    render_footer_line, footer_height, shortcut_overlay_lines,
    render_footer_from_props, FOOTER_INDENT_COLS,
)


class FooterTest(unittest.TestCase):
    def test_context_window_line_percent(self):
        self.assertEqual(context_window_line(72, None), "72% context left")
        self.assertEqual(context_window_line(0, None), "0% context left")
        self.assertEqual(context_window_line(100, None), "100% context left")
        self.assertEqual(context_window_line(150, None), "100% context left")
        self.assertEqual(context_window_line(-5, None), "0% context left")

    def test_context_window_line_tokens(self):
        self.assertEqual(context_window_line(None, 500), "500 used")
        self.assertEqual(context_window_line(None, 1500), "1.5k used")
        self.assertEqual(context_window_line(None, 1000000), "1.0M used")

    def test_context_window_line_fallback(self):
        self.assertEqual(context_window_line(None, None), "100% context left")

    def test_quit_shortcut_reminder(self):
        self.assertEqual(quit_shortcut_reminder_line("ctrl+c"), "ctrl+c again to quit")

    def test_esc_hint_primed(self):
        self.assertEqual(esc_hint_line(True), "esc again to edit previous message")

    def test_esc_hint_idle(self):
        self.assertEqual(esc_hint_line(False), "esc esc to edit previous message")

    def test_left_side_shortcuts(self):
        state = LeftSideState(hint=SummaryHintKind.SHORTCUTS, show_cycle_hint=False)
        result = left_side_line(None, state)
        self.assertEqual(result, "? for shortcuts")

    def test_left_side_queue_message(self):
        state = LeftSideState(hint=SummaryHintKind.QUEUE_MESSAGE, show_cycle_hint=False)
        result = left_side_line(None, state)
        self.assertEqual(result, "tab to queue message")

    def test_left_side_queue_short(self):
        state = LeftSideState(hint=SummaryHintKind.QUEUE_SHORT, show_cycle_hint=False)
        result = left_side_line(None, state)
        self.assertEqual(result, "tab to queue")

    def test_left_side_with_mode_indicator(self):
        state = LeftSideState(hint=SummaryHintKind.SHORTCUTS, show_cycle_hint=False)
        result = left_side_line(CollaborationModeIndicator.PLAN, state)
        self.assertEqual(result, "? for shortcuts · Plan mode")

    def test_left_side_mode_with_cycle_hint(self):
        state = LeftSideState(hint=SummaryHintKind.NONE, show_cycle_hint=True)
        result = left_side_line(CollaborationModeIndicator.PLAN, state)
        self.assertIn("shift+tab to cycle", result)

    def test_footer_from_props_empty(self):
        props = FooterProps(mode=FooterMode.COMPOSER_EMPTY)
        lines = footer_from_props_lines(props, show_shortcuts_hint=True)
        self.assertEqual(lines[0], "? for shortcuts")

    def test_footer_from_props_draft_running(self):
        props = FooterProps(mode=FooterMode.COMPOSER_HAS_DRAFT)
        lines = footer_from_props_lines(props, show_queue_hint=True)
        self.assertEqual(lines[0], "tab to queue message")

    def test_footer_from_props_quit(self):
        props = FooterProps(mode=FooterMode.QUIT_SHORTCUT_REMINDER)
        lines = footer_from_props_lines(props)
        self.assertIn("again to quit", lines[0])

    def test_shows_passive_footer_empty(self):
        props = FooterProps(mode=FooterMode.COMPOSER_EMPTY)
        self.assertTrue(shows_passive_footer_line(props))

    def test_shows_passive_footer_draft_task_running(self):
        props = FooterProps(mode=FooterMode.COMPOSER_HAS_DRAFT, is_task_running=True)
        self.assertFalse(shows_passive_footer_line(props))

    def test_shows_passive_footer_draft_no_task(self):
        props = FooterProps(mode=FooterMode.COMPOSER_HAS_DRAFT, is_task_running=False)
        self.assertTrue(shows_passive_footer_line(props))

    def test_passive_status_line(self):
        props = FooterProps(
            mode=FooterMode.COMPOSER_EMPTY,
            status_line_value="model · branch",
            status_line_enabled=True,
        )
        result = passive_footer_status_line(props)
        self.assertEqual(result, "model · branch")

    def test_passive_status_line_with_agent_label(self):
        props = FooterProps(
            mode=FooterMode.COMPOSER_EMPTY,
            status_line_value="model · branch",
            status_line_enabled=True,
            active_agent_label="agent-name",
        )
        result = passive_footer_status_line(props)
        self.assertEqual(result, "model · branch · agent-name")

    def test_can_show_left_with_context_fits(self):
        self.assertTrue(can_show_left_with_context(80, 20, 15))

    def test_can_show_left_with_context_too_wide(self):
        self.assertFalse(can_show_left_with_context(40, 30, 20))

    def test_render_footer_line(self):
        self.assertEqual(render_footer_line("hello"), "  hello")

    def test_footer_height_normal(self):
        props = FooterProps(mode=FooterMode.COMPOSER_EMPTY)
        self.assertEqual(footer_height(props), 1)

    def test_footer_height_shortcut_overlay(self):
        props = FooterProps(mode=FooterMode.SHORTCUT_OVERLAY)
        self.assertEqual(footer_height(props), 4)

    def test_shortcut_overlay_has_entries(self):
        lines = shortcut_overlay_lines()
        self.assertGreater(len(lines), 1)
        self.assertIn("commands", lines[0])

    def test_render_footer_active_task(self):
        props = FooterProps(
            mode=FooterMode.COMPOSER_HAS_DRAFT, is_task_running=True,
            status_line_value="m · b", status_line_enabled=True,
        )
        lines = render_footer_from_props(props, show_queue_hint=True)
        self.assertGreater(len(lines), 0)

    def test_render_footer_idle(self):
        props = FooterProps(
            mode=FooterMode.COMPOSER_EMPTY, is_task_running=False,
            status_line_value="m · b", status_line_enabled=True,
        )
        lines = render_footer_from_props(props, show_shortcuts_hint=True)
        self.assertGreaterEqual(len(lines), 1)

    def test_left_side_plan_mode_label(self):
        indicator = CollaborationModeIndicator.PLAN
        self.assertIn("Plan", indicator.label(True))
        self.assertIn("shift+tab to cycle", indicator.label(True))

    def test_left_side_execute_label(self):
        indicator = CollaborationModeIndicator.EXECUTE
        self.assertIn("Execute", indicator.label(False))


if __name__ == "__main__":
    unittest.main()
