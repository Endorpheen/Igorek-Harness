import re
import unittest
from factory.core.tui.exec_cell.render import (
    render_tool_call, _bullet, _format_duration, _tool_arg_preview,
)

def _strip_ansi(text: str) -> str:
    return re.sub(r'\033\[[0-9;]*m', '', text)

class ExecCellTest(unittest.TestCase):
    def test_bullet_success(self):
        self.assertEqual(_strip_ansi(_bullet(True)), "•")

    def test_bullet_failure(self):
        self.assertEqual(_strip_ansi(_bullet(False)), "•")

    def test_bullet_running(self):
        b = _strip_ansi(_bullet(None))
        self.assertIn(b, ("•", "◦"))

    def test_format_duration_seconds(self):
        self.assertEqual(_format_duration(0.5), "0.5s")
        self.assertEqual(_format_duration(5.0), "5.0s")

    def test_format_duration_minutes(self):
        self.assertEqual(_format_duration(125.0), "2m 05s")

    def test_format_duration_small(self):
        self.assertEqual(_format_duration(0.05), "<0.1s")

    def test_tool_arg_bash(self):
        self.assertEqual(_tool_arg_preview("Bash", {"command": "ls -la"}), "ls -la")

    def test_tool_arg_read(self):
        self.assertEqual(_tool_arg_preview("Read", {"file_path": "/tmp/x.py"}), "/tmp/x.py")

    def test_tool_arg_write(self):
        self.assertEqual(_tool_arg_preview("Write", {"file_path": "out.txt"}), "out.txt")

    def test_tool_arg_grep(self):
        self.assertEqual(_tool_arg_preview("Grep", {"pattern": "TODO", "path": "src/"}), "TODO src/")

    def test_tool_arg_web(self):
        self.assertEqual(_tool_arg_preview("WebSearch", {"query": "python"}), "python")

    def test_render_running(self):
        lines = render_tool_call("Bash", {"command": "ls"}, is_running=True)
        self.assertIn("Running", lines[0])
        self.assertIn("Bash", lines[0])
        self.assertIn("ls", lines[0])

    def test_render_done(self):
        lines = render_tool_call("Read", {"file_path": "x.py"}, status="done", elapsed=0.3)
        self.assertIn("Ran", lines[0])
        self.assertIn("Read", lines[0])
        self.assertIn("x.py", lines[0])
        self.assertIn("done", lines[1])
        self.assertIn("0.3s", lines[1])

    def test_render_error(self):
        lines = render_tool_call("Bash", {"command": "bad"}, status="error", elapsed=1.0)
        self.assertIn("error", lines[1])

    def test_render_with_output(self):
        lines = render_tool_call("Bash", {"command": "echo hi"}, status="done", elapsed=0.1,
                                 output="hello\nworld\nline3\nline4\nline5\nline6\nline7")
        clean = [_strip_ansi(l) for l in lines]
        self.assertIn("hello", clean[2] if len(clean) > 2 else "")
        has_omitted = any("…" in l for l in clean)
        self.assertTrue(has_omitted)

    def test_render_header_starts_at_viewport_edge_like_codex(self):
        lines = render_tool_call("web_extract", {"url": "https://example.com"}, status="done", elapsed=1.4)
        clean = _strip_ansi(lines[0])
        self.assertTrue(clean.startswith("• Ran web_extract"))

    def test_render_long_output_wraps_under_tree_gutter(self):
        lines = render_tool_call(
            "Read",
            {"file_path": "x.txt"},
            status="done",
            elapsed=0.1,
            output="word " * 30,
            width=36,
        )
        clean = [_strip_ansi(line) for line in lines]
        output_lines = clean[2:]
        self.assertGreater(len(output_lines), 1)
        self.assertTrue(all(line.startswith("    ") or line.startswith("  └ ") for line in output_lines))

    def test_no_args(self):
        lines = render_tool_call("Unknown", {}, status="done", elapsed=0.1)
        self.assertIn("Unknown", lines[0])

    def test_render_exit_code(self):
        lines = render_tool_call("Bash", {"command": "cmd"}, status="exit", elapsed=2.0)
        self.assertIn("exit", lines[1])

    def test_render_no_status(self):
        lines = render_tool_call("Read", {"file_path": "f.py"})
        self.assertEqual(len(lines), 1)

    def test_render_long_command_truncated(self):
        long_cmd = "x" * 200
        lines = render_tool_call("Bash", {"command": long_cmd}, status="done", elapsed=0.1)
        clean = _strip_ansi(lines[0])
        self.assertLess(len(clean.split("(")[1]), 110)


if __name__ == "__main__":
    unittest.main()
