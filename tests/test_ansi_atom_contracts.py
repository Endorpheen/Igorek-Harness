import unittest

from factory.core.tui.markdown_render import _ansi_style_atom_stream, _styled_slice

BOLD = chr(27) + "[1m"
RESET = chr(27) + "[0m"


class AnsiAtomContractTests(unittest.TestCase):
    def test_ansi_style_atom_stream_splits_chars_and_styles(self):
        chars, styles = _ansi_style_atom_stream(BOLD + "ABC" + RESET + "Z")
        self.assertEqual(chars, list("ABCZ"))
        self.assertEqual(styles, [BOLD, BOLD, BOLD, ""])
        empty_chars, empty_styles = _ansi_style_atom_stream("")
        self.assertEqual(empty_chars, [])
        self.assertEqual(empty_styles, [])

    def test_styled_slice_rebuilds_substring(self):
        chars, styles = _ansi_style_atom_stream(BOLD + "ABC" + RESET + "Z")
        self.assertEqual(_styled_slice(chars, styles, 1, 3), BOLD + "BC" + RESET)
        self.assertEqual(_styled_slice(chars, styles, 3, 4), "Z")
        self.assertEqual(chars, list("ABCZ"))
        self.assertEqual(styles, [BOLD, BOLD, BOLD, ""])


if __name__ == "__main__":
    unittest.main()
