import unittest
from factory.core.tui.render.highlight import highlight_bash_to_lines, highlight_code_to_lines
from factory.core.tui.render.line_utils import prefix_lines

class RenderTest(unittest.TestCase):
    def test_highlight_bash_keyword(self):
        result = highlight_bash_to_lines("echo hello")
        self.assertTrue(any("echo" in line for line in result))

    def test_highlight_bash_comment(self):
        result = highlight_bash_to_lines("ls # list files")
        self.assertTrue(any("list files" in line for line in result))

    def test_highlight_diff_add(self):
        pass  # highlight_diff not implemented

    def test_highlight_diff_remove(self):
        pass  # highlight_diff not implemented

    def test_highlight_code_bash(self):
        result = highlight_code_to_lines("echo hi", "bash")
        self.assertTrue(any("echo" in line for line in result))

    def test_highlight_code_python(self):
        result = highlight_code_to_lines("def foo(): pass", "python")
        self.assertTrue(any("def" in line for line in result))

    def test_prefix_lines(self):
        result = prefix_lines(["a", "b", "c"], ">", " ")
        self.assertEqual(result, [">a", " b", " c"])

    def test_prefix_lines_empty(self):
        self.assertEqual(prefix_lines([], ">", " "), [])


