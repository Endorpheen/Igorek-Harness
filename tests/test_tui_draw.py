import re
import shutil
import unittest
from unittest import mock

from factory.core.tui.resume_picker import PickerRow, PickerState, _render_picker
from factory.core.tui.tui import Tui, _fit_ansi_line


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class _FakeStdout:
    def __init__(self):
        self.output = ""

    def isatty(self):
        return True

    def write(self, text):
        self.output += text

    def flush(self):
        pass


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class TuiDrawTest(unittest.TestCase):
    def test_fit_ansi_line_truncates_by_visible_width(self):
        line = "\x1b[7mabcdef\x1b[0m"

        fitted = _fit_ansi_line(line, 4)

        self.assertEqual(_strip_ansi(fitted), "abcd")
        self.assertTrue(fitted.endswith("\x1b[0m"))

    def test_fit_ansi_line_pads_plain_line_to_width(self):
        self.assertEqual(_fit_ansi_line("abc", 5), "abc  ")

    def test_fit_ansi_line_replaces_embedded_newlines(self):
        fitted = _fit_ansi_line("abc\ndef", 9)

        self.assertEqual(_strip_ansi(fitted), "abc def  ")

    def test_resume_picker_selected_row_fits_single_terminal_row(self):
        state = PickerState()
        state.set_size(48, 12)
        state.set_rows([
            PickerRow(
                session_id="session-one",
                path="/tmp/session-one.jsonl",
                preview="создай очень длинное описание сессии\nкоторое раньше переносилось ниже",
                model="deepseek-v4-long-name",
                message_count=12,
                updated_at=1715073600,
            ),
            PickerRow(
                session_id="session-two",
                path="/tmp/session-two.jsonl",
                preview="next row",
                model="model",
                message_count=1,
                updated_at=1715070000,
            ),
        ])

        selected = _render_picker(state)[3]
        fitted = _fit_ansi_line(selected, state._width)

        self.assertLessEqual(len(_strip_ansi(fitted)), state._width)
        self.assertTrue(fitted.endswith("\x1b[0m"))

    def test_draw_does_not_emit_newline_from_frame_rows(self):
        stdout = _FakeStdout()
        tui = Tui()

        with mock.patch("factory.core.tui.tui.sys.stdout", stdout), \
             mock.patch(
                 "factory.core.tui.tui.shutil.get_terminal_size",
                 return_value=shutil.os.terminal_size((20, 5)),
             ):
            tui.draw(2, lambda vp, buf: buf.extend(["abc\ndef", "second"]), force=True)

        self.assertNotIn("\n", stdout.output)
        self.assertIn("abc def", stdout.output)


if __name__ == "__main__":
    unittest.main()
