import unittest
from factory.core.tui.bottom_pane import BottomPane
from factory.core.tui.status_indicator_widget import StatusIndicatorWidget
from factory.core.tui.bottom_pane.chat_composer import ChatComposer
from factory.core.tui.render.renderable import Rect

class BottomPaneTest(unittest.TestCase):
    def setUp(self):
        self.bp = BottomPane()

    def test_create(self):
        self.assertIsNotNone(self.bp.composer)
        self.assertIsNotNone(self.bp.pending_preview)
        self.assertIsNone(self.bp.status)

    def test_set_status(self):
        w = StatusIndicatorWidget()
        self.bp.set_status(w)
        self.assertIsNotNone(self.bp.status)

    def test_build_layout_empty(self):
        flex = self.bp.build_flex_layout()
        self.assertGreaterEqual(len(flex._children), 2)

    def test_build_layout_with_status(self):
        self.bp.set_status(StatusIndicatorWidget())
        flex = self.bp.build_flex_layout()
        self.assertGreaterEqual(len(flex._children), 3)

    def test_desired_height(self):
        h = self.bp.desired_height(80)
        self.assertGreater(h, 3)

    def test_render(self):
        buf = []
        self.bp.render(Rect(0, 0, 80, 10), buf)
        self.assertGreater(len(buf), 0)

    def test_cursor_pos(self):
        cp = self.bp.cursor_pos(Rect(0, 0, 80, 10))
        self.assertIsNotNone(cp)

    def test_update_task_running(self):
        self.bp.update_task_running(True)
        self.assertTrue(self.bp.footer_props.is_task_running)

    def test_set_context_window(self):
        self.bp.set_context_window(50, 16000)
        self.assertEqual(self.bp.footer_props.context_window_percent, 50)

    def test_set_status_line(self):
        self.bp.set_status_line("model · branch")
        self.assertEqual(self.bp.footer_props.status_line_value, "model · branch")

    def test_composer_integration(self):
        self.bp.composer.set_text("hello")
        buf = []
        self.bp.render(Rect(0, 0, 80, 10), buf)
        self.assertTrue(any("hello" in b for b in buf))

    def test_pending_preview_visible(self):
        self.bp.pending_preview.add_queued("test message")
        self.assertFalse(self.bp.pending_preview.is_empty())
