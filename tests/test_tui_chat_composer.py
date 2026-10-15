import unittest
from factory.core.tui.bottom_pane.chat_composer import ChatComposer, ActivePopup, LIVE_PREFIX_COLS
from factory.core.tui.render.renderable import Rect
from factory.core.tui.bottom_pane.footer import FooterMode


class ChatComposerTest(unittest.TestCase):
    def setUp(self):
        self.c = ChatComposer()

    def test_insert_char(self):
        self.c.insert_char("h")
        self.c.insert_char("i")
        self.assertEqual(self.c.text, "hi")
        self.assertEqual(self.c.cursor, 2)

    def test_insert_newline(self):
        self.c.set_text("hello")
        self.c.cursor = 5
        self.c.insert_newline()
        self.assertEqual(self.c.text, "hello\n")

    def test_backspace(self):
        self.c.set_text("abc")
        self.c.cursor = 3
        self.c.backspace()
        self.assertEqual(self.c.text, "ab")

    def test_move_cursor(self):
        self.c.set_text("abc")
        self.c.move_cursor_left()
        self.assertEqual(self.c.cursor, 2)
        self.c.move_cursor_right()
        self.assertEqual(self.c.cursor, 3)

    def test_clear(self):
        self.c.set_text("hello")
        self.c.clear()
        self.assertEqual(self.c.text, "")

    def test_submit_callback(self):
        submitted = []
        self.c.set_on_submit(lambda t: submitted.append(t))
        self.c.set_text("hello world")
        self.c.submit()
        self.assertEqual(submitted, ["hello world"])
        self.assertEqual(self.c.text, "")

    def test_interrupt_callback(self):
        interrupted = [False]
        self.c.set_on_interrupt(lambda: interrupted.__setitem__(0, True))
        self.c.interrupt()
        self.assertTrue(interrupted[0])

    def test_layout_areas(self):
        area = Rect(0, 0, 80, 10)
        areas = self.c.layout_areas(area)
        self.assertIn("composer", areas)
        self.assertIn("textarea", areas)
        self.assertIn("popup", areas)
        self.assertGreater(areas["composer"].height, 2)
        self.assertGreater(areas["textarea"].width, 0)

    def test_desired_height(self):
        self.assertGreater(self.c.desired_height(80), 0)

    def test_render_idle(self):
        buf = []
        self.c.render(Rect(0, 0, 80, 6), buf)
        self.assertGreater(len(buf), 0)

    def test_render_with_text(self):
        self.c.set_text("hello")
        buf = []
        self.c.render(Rect(0, 0, 80, 6), buf)
        has_text = any("hello" in b for b in buf)
        self.assertTrue(has_text)

    def test_render_disabled(self):
        self.c.input_enabled = False
        buf = []
        self.c.render(Rect(0, 0, 80, 6), buf)
        self.assertIn("disabled", buf[0])

    def test_cursor_pos(self):
        self.c.set_text("ab")
        self.c.cursor = 1
        area = Rect(0, 0, 80, 10)
        cp = self.c.cursor_pos(area)
        self.assertIsNotNone(cp)
        self.assertEqual(cp[0], area.x + LIVE_PREFIX_COLS + 1)  # x
        self.assertEqual(cp[1], area.y + 1)  # y

    def test_handle_key_char(self):
        self.c.handle_key_event("x")
        self.assertEqual(self.c.text, "x")

    def test_handle_key_enter(self):
        submitted = []
        self.c.set_on_submit(lambda t: submitted.append(t))
        self.c.set_text("test")
        self.c.handle_key_event("enter")
        self.assertEqual(submitted, ["test"])

    def test_handle_key_ctrl_j(self):
        self.c.set_text("a")
        self.c.handle_key_event("c-j")
        self.assertIn("\n", self.c.text)

    def test_handle_key_slash_empty(self):
        self.c.clear()
        self.c.handle_key_event("/")
        self.assertEqual(self.c.active_popup, ActivePopup.COMMAND)

    def test_handle_key_question_empty(self):
        self.c.clear()
        self.c.handle_key_event("?")
        self.assertEqual(self.c.footer_props.mode, FooterMode.SHORTCUT_OVERLAY)
        self.c.handle_key_event("?")
        self.assertEqual(self.c.footer_props.mode, FooterMode.COMPOSER_EMPTY)

    def test_handle_key_escape_closes_popup(self):
        self.c.active_popup = ActivePopup.COMMAND
        self.c.handle_key_event("escape")
        self.assertEqual(self.c.active_popup, ActivePopup.NONE)

    def test_handle_key_tab_with_text(self):
        submitted = []
        self.c.set_on_queue(lambda t: submitted.append(t))
        self.c.set_text("queue me")
        self.c.handle_key_event("tab")
        self.assertEqual(submitted, ["queue me"])

    def test_enter_while_idle_calls_submit(self):
        submitted = []
        self.c.set_on_submit(lambda t: submitted.append(t))
        self.c.set_text("idle message")
        self.c.handle_key_event("enter")
        self.assertEqual(submitted, ["idle message"])
        self.assertEqual(self.c.text, "")

    def test_ctrl_j_inserts_newline(self):
        self.c.set_text("line1")
        self.c.cursor = 5
        self.c.handle_key_event("c-j")
        self.assertEqual(self.c.text, "line1\n")
        self.assertEqual(self.c.cursor, 6)

    def test_shift_enter_inserts_newline(self):
        self.c.set_text("line1")
        self.c.cursor = 5
        self.c.handle_key_event("shift-enter")
        self.assertEqual(self.c.text, "line1\n")
        self.assertEqual(self.c.cursor, 6)

    def test_up_at_cursor_zero_recalls_history(self):
        self.c.history.add_local({"text": "previous"})
        self.c.set_text("")
        self.c.cursor = 0
        self.c.handle_key_event("up")
        self.assertEqual(self.c.text, "previous")

    def test_up_at_cursor_end_recalls_history(self):
        self.c.history.add_local({"text": "old msg"})
        self.c.set_text("current")
        self.c.cursor = 7  # at end
        self.c.handle_key_event("up")
        self.assertEqual(self.c.text, "old msg")

    def test_up_mid_text_moves_cursor_not_history(self):
        self.c.history.add_local({"text": "history item"})
        self.c.set_text("line1\nline2\nline3")
        self.c.cursor = 10  # in middle of line2
        self.c.handle_key_event("up")
        # Should move cursor up, not recall history
        self.assertEqual(self.c.text, "line1\nline2\nline3")
        self.assertNotEqual(self.c.text, "history item")

    def test_down_at_cursor_end_recalls_next(self):
        self.c.history.add_local({"text": "first"})
        self.c.history.add_local({"text": "second"})
        self.c.set_text("")
        self.c.cursor = 0
        self.c.handle_key_event("up")  # recall "second" (newest)
        self.assertEqual(self.c.text, "second")
        # Down at end of text: moves forward in history
        self.c.handle_key_event("down")
        # After cycling past end, restores original empty text
        self.assertEqual(self.c.text, "")

    def test_queue_callback_during_submit(self):
        queued = []
        self.c.set_on_queue(lambda t: queued.append(t))
        self.c.set_text("queued text")
        self.c.queue()
        self.assertEqual(queued, ["queued text"])

    def test_multiline_cursor_up_down(self):
        self.c.set_text("aaa\nbbb\nccc")
        self.c.cursor = 5  # middle of 'bbb' (col 1)
        self.c.move_cursor_up()
        self.assertEqual(self.c.cursor, 1)  # col 1 of 'aaa'
        self.c.move_cursor_down()
        self.assertEqual(self.c.cursor, 5)  # back to 'bbb'


if __name__ == "__main__":
    unittest.main()
