import re
import unittest

from factory.core.tui.cells.history_cell import DiffCell


_ANSI_RE = re.compile(r"\033\[[0-9;]*[A-Za-z]")


def _clean(line: str) -> str:
    return _ANSI_RE.sub("", line)


class DiffCellTest(unittest.TestCase):
    """Unit tests for DiffCell real-file-line-number rendering."""

    def test_without_start_line_falls_back_to_fragment_index(self):
        """Backwards-compatible: no start_line keeps 1-based fragment indexing."""
        lines = DiffCell("alpha\nbeta\ngamma\n", "alpha\nBETA\ngamma\n", "demo.py").display_lines(80)
        clean = [_clean(line) for line in lines]

        self.assertTrue(any("└ demo.py" in line for line in clean))
        self.assertFalse(any("@@" in line for line in clean))
        # Fragment-relative numbering: the changed line shows 2 (beta is the 2nd line).
        # Format is `<sign>{:4d}` where sign is U+2212 for delete / '+' for insert.
        self.assertTrue(any(re.search(r"\u2212\s+2\s+beta", line) for line in clean))
        self.assertTrue(any(re.search(r"\+\s+2\s+BETA", line) for line in clean))

    def test_with_start_line_shows_real_file_line_numbers(self):
        """start_line=458 → gutter shows real file lines (not 1) for diff entries."""
        lines = DiffCell(
            "x = 1\nx = 2\nx = 3\n",
            "x = 1\nx = 99\nx = 3\n",
            "app.py",
            start_line=458,
        ).display_lines(80)
        clean = [_clean(line) for line in lines]

        # Hunk header in Codex style
        self.assertTrue(any("@@ app.py:458-460 @@" in line for line in clean))
        # The changed line is fragment index 1 → file line 458+1 = 459.
        self.assertTrue(any(re.search(r"\u2212\s+459\s+x = 2", line) for line in clean))
        self.assertTrue(any(re.search(r"\+\s+459\s+x = 99", line) for line in clean))
        # And explicitly NOT the fragment-relative indices for the changed line
        self.assertFalse(any(re.search(r"\u2212\s+2\s+x = 2", line) for line in clean))

    def test_with_start_line_renders_hunk_header_without_replace_all(self):
        lines = DiffCell("a\nb\n", "a\nB\n", "x.py", start_line=10).display_lines(80)
        clean = [_clean(line) for line in lines]

        self.assertTrue(any("@@ x.py:10-11 @@" in line for line in clean))
        self.assertFalse(any("replace_all" in line for line in clean))

    def test_with_start_line_and_replace_all_marks_header(self):
        lines = DiffCell(
            "a\n",
            "A\n",
            "x.py",
            start_line=42,
            replace_all=True,
        ).display_lines(80)
        clean = [_clean(line) for line in lines]

        self.assertTrue(any("@@ x.py:42-42 (replace_all) @@" in line for line in clean))
        # The replace_all marker must not leak into a non-replace_all header
        self.assertTrue(any("@@" in line and "(replace_all)" in line for line in clean))

    def test_with_start_line_but_empty_diff_omits_hunk_header(self):
        lines = DiffCell("", "", "x.py", start_line=10).display_lines(80)
        clean = [_clean(line) for line in lines]

        # The summary line is always present, but no hunk header for empty content
        self.assertFalse(any("@@" in line for line in clean))

    def test_with_start_line_uses_filename_basename_in_header(self):
        lines = DiffCell(
            "a\n",
            "A\n",
            "/some/long/path/to/x.py",
            start_line=5,
        ).display_lines(80)
        clean = [_clean(line) for line in lines]

        self.assertTrue(any("@@ x.py:5-5 @@" in line for line in clean))


if __name__ == "__main__":
    unittest.main()
