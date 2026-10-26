import unittest

from factory.core.smarthelp import _one_line


class TestToolPreviewContracts(unittest.TestCase):

    def test_one_line_normalization(self):
        self.assertEqual(_one_line(" a\n b\t c "), "a b c")
        self.assertEqual(_one_line(None), "")
        self.assertEqual(_one_line(42), "42")

    def test_one_line_truncation_and_unicode(self):
        result = _one_line("x" * 100, 80)
        self.assertEqual(result, "x" * 77 + "...")
        self.assertEqual(len(result), 80)
        self.assertEqual(_one_line("Привет 🐙"), "Привет 🐙")
