import subprocess
import sys
import unittest
from pathlib import Path
from factory.core.tui.cells.history_cell import TextCell, ToolCallCell, StatusCell, MarkdownCell, SeparatorCell

class HistoryCellTest(unittest.TestCase):
    def test_text_cell_user(self):
        c = TextCell("hello", role="user")
        lines = c.display_lines(80)
        self.assertIn("  hello", lines)

    def test_text_cell_assistant(self):
        c = TextCell("world")
        lines = c.display_lines(80)
        self.assertIn("world", lines)

    def test_text_cell_multiline(self):
        c = TextCell("a\nb\nc", role="user")
        lines = c.display_lines(80)
        self.assertEqual(len(lines), 3)

    def test_tool_call_cell(self):
        c = ToolCallCell("Read", {"file_path": "x.py"}, status="done", elapsed=0.5)
        lines = c.display_lines(80)
        self.assertGreater(len(lines), 0)
        self.assertIn("Read", lines[0])

    def test_status_cell(self):
        c = StatusCell("Working (15s · esc to interrupt)")
        lines = c.display_lines(80)
        self.assertIn("Working", lines[0])

    def test_markdown_cell(self):
        c = MarkdownCell("**bold** text")
        lines = c.display_lines(80)
        self.assertIn("bold", lines[0])
        self.assertIn("\033[1m", lines[0])  # ANSI bold

    def test_markdown_cell_can_render_codex_style_marker(self):
        c = MarkdownCell("answer", marker=True)
        lines = c.display_lines(80)
        self.assertTrue(lines[0].startswith("• answer"))

    def test_markdown_cell_renders_tables_with_ascii_pipes(self):
        c = MarkdownCell("| A | B |\n|---|---|\n| 1 | 2 |", marker=True)
        lines = c.display_lines(80)
        joined = "\n".join(lines)
        self.assertIn("A", joined)

    def test_markdown_cell_wraps_long_paragraph_to_width(self):
        import re

        ansi = re.compile(r"\033\[[0-9;]*[A-Za-z]")
        c = MarkdownCell(" ".join(["слово"] * 60))
        lines = c.display_lines(40)
        plain = [ansi.sub("", line) for line in lines]
        self.assertTrue(all(len(line) <= 40 for line in plain), plain)
        self.assertGreater(len([l for l in plain if l.strip()]), 1)

    def test_separator_cell_leaves_right_edge_margin(self):
        c = SeparatorCell()
        lines = c.display_lines(20)
        self.assertEqual(len(lines[0].replace("\033[2m", "").replace("\033[0m", "")), 16)

    def test_separator_cell_honors_explicit_length(self):
        c = SeparatorCell(length=8)
        lines = c.display_lines(20)
        self.assertEqual(len(lines[0].replace("\033[2m", "").replace("\033[0m", "")), 8)

    def test_labeled_separator_renders_label_in_middle(self):
        # Codex FinalMessageSeparator port: the line is ─ {label} ──────,
        # with the label flanked by dashes and the remainder filled with
        # box-drawing dashes, all dim.
        c = SeparatorCell(full_width=True, label="Worked for 14s")
        lines = c.display_lines(80)
        self.assertEqual(len(lines), 1)
        plain = lines[0].replace("\033[2m", "").replace("\033[0m", "")
        self.assertIn("Worked for 14s", plain)
        self.assertTrue(plain.startswith("─ Worked for 14s ─"))
        # Visible length must respect the Rich cell_len budget (width - 6)
        self.assertEqual(len(plain), 74)

    def test_labeled_separator_does_not_wrap_via_rich_console_print(self):
        import io
        from rich.console import Console
        c = SeparatorCell(full_width=True, label="Worked for 1m 05s")
        for width in (80, 100, 120, 200):
            lines = c.display_lines(width)
            self.assertEqual(len(lines), 1, f"width={width} must yield 1 line")
            buf = io.StringIO()
            console = Console(
                file=buf, width=width, highlight=False,
                no_color=False, emoji=False,
            )
            console.print(lines[0], markup=False)
            non_empty_rows = [r for r in buf.getvalue().split("\n") if r]
            self.assertEqual(
                len(non_empty_rows), 1,
                f"labeled separator wrapped to {len(non_empty_rows)} rows "
                f"at width={width}; output={buf.getvalue()!r}",
            )

    def test_full_width_separator_does_not_wrap_via_rich_console_print(self):
        # Regression: a full-width dim separator printed through Rich
        # ``console.print(line, markup=False)`` must stay on a single physical
        # row. Rich's ``cell_len`` does not subtract the embedded ``\033[2m``/
        # ``\033[0m`` escape sequences, so it counts them as 6 visible columns
        # and wraps a line whose visible length exceeds ``width - 6``. This
        # test guards the structural invariant end-to-end via the real render
        # path used for scrollback emission.
        #
        # ``force_terminal`` is intentionally left at its default (False) so
        # that ``Console`` honours the explicit ``width`` argument; setting
        # ``force_terminal=True`` makes Rich fall back to a fixed 80-column
        # width regardless of the ``width`` kwarg, which would defeat the
        # per-width coverage of this regression.
        import io
        from rich.console import Console
        c = SeparatorCell(full_width=True)
        for width in (80, 100, 120, 200):
            lines = c.display_lines(width)
            self.assertEqual(len(lines), 1, f"width={width} must yield 1 line")
            buf = io.StringIO()
            console = Console(
                file=buf, width=width, highlight=False,
                no_color=False, emoji=False,
            )
            console.print(lines[0], markup=False)
            non_empty_rows = [r for r in buf.getvalue().split("\n") if r]
            self.assertEqual(
                len(non_empty_rows), 1,
                f"full-width separator wrapped to {len(non_empty_rows)} rows "
                f"at width={width}; output={buf.getvalue()!r}",
            )

    def test_desired_height(self):
        c = TextCell("line1\nline2\nline3")
        self.assertEqual(c.desired_height(80), 3)

    def test_tool_call_no_status(self):
        c = ToolCallCell("Bash", {"command": "ls"})
        lines = c.display_lines(80)
        self.assertEqual(len(lines), 1)

    def test_tool_call_uses_canonical_renderer_when_legacy_exec_package_is_missing(self):
        root = Path(__file__).resolve().parents[1]
        script = """
import builtins
import sys

sys.path.insert(0, {root!r})
sys.path.insert(0, {factory_root!r})
real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "exec_cell.render" or name.startswith("core.tui.exec_cell"):
        raise ModuleNotFoundError(name)
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
from core.tui.cells.history_cell import ToolCallCell
lines = ToolCallCell("web_search", {{"query": "test"}}, is_running=True).display_lines(80)
assert lines and "web_search" in lines[0]
""".format(root=str(root), factory_root=str(root / "factory"))
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
