"""Tests for long-line wrapping in the TUI transcript cells.

Before the fix, `ErrorCell`/`TextCell` emitted one long line that the
terminal clipped at the right edge (the provider's HTTP error text was cut
mid-word). They must wrap to the available width instead.
"""

import re
import unittest

from factory.core.tui.cells.history_cell import ErrorCell, TextCell

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class ErrorCellWrapTest(unittest.TestCase):
    def test_long_error_wraps_and_stays_within_width(self):
        text = "HTTP 400: " + ("x" * 400)
        lines = ErrorCell(text).display_lines(40)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertIn("\033[31m", line)
            self.assertIn("\033[0m", line)
            self.assertLessEqual(len(_ANSI.sub("", line)), 40)

    def test_short_error_single_line(self):
        lines = ErrorCell("boom").display_lines(80)
        self.assertEqual(len(lines), 1)
        self.assertIn("boom", lines[0])

    def test_empty_error_single_red_line(self):
        self.assertEqual(ErrorCell("").display_lines(80), ["  \033[31m\033[0m"])

    def test_wraps_by_visible_width(self):
        # The two-space indent must be accounted for, so the wrapped chunks
        # never exceed the terminal width.
        lines = ErrorCell("word " * 60).display_lines(20)
        for line in lines:
            self.assertLessEqual(len(_ANSI.sub("", line)), 20)


class TextCellWrapTest(unittest.TestCase):
    def test_long_text_cell_wraps(self):
        lines = TextCell("abc " * 50).display_lines(30)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(len(line), 30)

    def test_user_cell_keeps_prefix_and_wraps(self):
        lines = TextCell("hello " * 20, role="user").display_lines(20)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertTrue(line.startswith("  "))
            self.assertLessEqual(len(line), 20)

    def test_short_text_single_line(self):
        self.assertEqual(TextCell("hi").display_lines(80), ["hi"])


if __name__ == "__main__":
    unittest.main()
