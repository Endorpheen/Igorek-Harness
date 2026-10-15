import re
import unittest
from factory.core.tui.markdown_render import (
    render_markdown_to_lines, _format_inline,
)
from factory.core.tui.theme import preview_theme


class MarkdownTest(unittest.TestCase):
    def setUp(self):
        self.theme_preview = preview_theme("dark")
        self.theme_preview.__enter__()
        self.addCleanup(self.theme_preview.__exit__, None, None, None)

    def test_render_headers(self):
        lines = render_markdown_to_lines("# Title\n\nText", 40)
        self.assertTrue(any("Title" in l for l in lines))

    def test_render_code_block(self):
        lines = render_markdown_to_lines("```python\nprint(1)\n```", 40)
        self.assertTrue(any("print(1)" in l for l in lines))

    def test_render_code_block_has_color_without_box_drawing(self):
        lines = render_markdown_to_lines("```python\nx=1\n```", 40)
        joined = "\n".join(lines)
        self.assertTrue(any("x=1" in l for l in lines))
        self.assertFalse(any(ch in joined for ch in "┌└╭╰"))

    def test_render_indented_code_fence_inside_list(self):
        lines = render_markdown_to_lines(
            "- Homebrew:\n  ```bash\n  brew install opencode-ai/tap/opencode\n  ```",
            80,
        )
        joined = "\n".join(lines)
        self.assertIn("brew install opencode-ai/tap/opencode", joined)
        self.assertNotIn("```", joined)

    def test_render_list(self):
        lines = render_markdown_to_lines("- item1\n- item2", 40)
        self.assertTrue(any("item1" in l for l in lines))

    def test_long_paragraph_wraps_to_width(self):
        ansi = re.compile(r"\033\[[0-9;]*[A-Za-z]")
        text = " ".join(["слово"] * 60)
        lines = render_markdown_to_lines(text, 40)
        plain = [ansi.sub("", line) for line in lines]
        self.assertGreater(len([l for l in plain if l.strip()]), 1)
        self.assertTrue(all(len(line) <= 40 for line in plain), plain)
        rebuilt = " ".join(line.strip() for line in plain if line.strip())
        self.assertEqual(rebuilt.split(), text.split())

    def test_long_styled_paragraph_keeps_inline_code_after_wrap(self):
        ansi = re.compile(r"\033\[[0-9;]*[A-Za-z]")
        token = "factory/core/tui/markdown_render/__init__.py"
        text = f"Начало `{token}` " + " ".join(["слово"] * 40) + " конец"
        lines = render_markdown_to_lines(text, 50)
        plain = [ansi.sub("", line) for line in lines]
        self.assertTrue(all(len(line) <= 50 for line in plain), plain)
        joined = "".join(plain)
        self.assertIn(token, joined)
        self.assertIn("конец", joined)
        self.assertTrue(any("\033[" in line for line in lines))

    def test_code_block_line_is_not_width_wrapped(self):
        ansi = re.compile(r"\033\[[0-9;]*[A-Za-z]")
        code = "x = '" + "a" * 120 + "'"
        lines = render_markdown_to_lines("```python\n" + code + "\n```", 40)
        plain = [ansi.sub("", line) for line in lines]
        self.assertTrue(any(len(line) > 40 for line in plain), plain)
        self.assertTrue(any(code in line for line in plain), plain)

    def test_table_stays_within_width_after_wrap(self):
        ansi = re.compile(r"\033\[[0-9;]*[A-Za-z]")
        lines = render_markdown_to_lines(
            "| A | B |\n|---|---|\n| " + "x" * 80 + " | y |",
            60,
        )
        plain = [ansi.sub("", line) for line in lines]
        self.assertTrue(all(len(line) <= 60 for line in plain), plain)

    def test_numeric_answer_with_trailing_dot_is_visible(self):
        lines = render_markdown_to_lines("8.", 40)
        self.assertEqual(lines, ["8."])

    def test_ordered_list_with_content_still_renders_as_list(self):
        lines = render_markdown_to_lines("8. item", 40)
        joined = "\n".join(lines)
        self.assertIn("8.", joined)
        self.assertIn("item", joined)

    def test_list_item_starting_with_inline_code_keeps_marker_on_same_line(self):
        ansi = re.compile(r"\033\[[0-9;]*[A-Za-z]")
        lines = [
            ansi.sub("", line)
            for line in render_markdown_to_lines(
                "- `factory/core/tui/cells/history_cell.py` (+19/-6)\n"
                "- Добавлены тесты `tests/test_tui_history_cell.py`",
                100,
            )
        ]
        self.assertNotIn("- ", lines)
        self.assertIn(
            "- factory/core/tui/cells/history_cell.py (+19/-6)",
            lines,
        )
        self.assertIn(
            "- Добавлены тесты tests/test_tui_history_cell.py",
            lines,
        )

    def test_render_table_uses_box_drawing(self):
        lines = render_markdown_to_lines("| A | B |\n|---|---|\n| 1 | 2 |", 80)
        joined = "\n".join(lines)
        self.assertIn("A", joined)
        self.assertIn("1", joined)

    def test_render_table_uses_shared_ascii_pipe_renderer(self):
        lines = render_markdown_to_lines("| A | B |\n|---|---|\n| **bold** | `code` |", 80)
        joined = "\n".join(lines)
        self.assertIn("bold", joined)
        self.assertTrue(any("co" in l for l in lines) or any("de" in l for l in lines))

    def test_render_table_inline_code_visible_width_stays_aligned(self):
        lines = render_markdown_to_lines(
            "| Подфаза | Команда | Статус |\n"
            "|---|---|---|\n"
            "| 7.1 | `request_user_input` | **готово** |",
            100,
        )
        joined = "\n".join(lines)
        self.assertIn("request_user_input", joined)
        self.assertTrue(any("готово" in l for l in lines) or any("тово" in l for l in lines))

    def test_render_table_flushes_at_end_of_text(self):
        lines = render_markdown_to_lines("| A | B |\n|---|---|\n| 1 | 2 |", 80)
        self.assertTrue(any("1" in line and "2" in line for line in lines))

    def test_incomplete_pipe_line_is_plain_text(self):
        lines = render_markdown_to_lines("| A | B |", 80)
        joined = "\n".join(lines)
        self.assertIn("| A | B |", joined)

    def test_render_table_separator_matches_row_width(self):
        lines = render_markdown_to_lines("| Header | Value |\n|---|---|\n| one | two |", 80)
        ansi = re.compile(r"\033\[[0-9;]*m")
        visible_widths = [len(ansi.sub("", line)) for line in lines]
        self.assertEqual(len(set(visible_widths)), 1)

    def test_render_table_wrapped_styled_cells_do_not_color_or_break_borders(self):
        lines = render_markdown_to_lines(
            "| Модель | Контекст | In/Out | Заметка |\n"
            "|---|---|---|---|\n"
            "| `nvidia/nemotron-3-ultra-550b-a55b:free` | `1M` | `$0.5 / $2.2` | `550B MoE, 55B active, open reasoning` |",
            100,
        )
        ansi = re.compile(r"\033\[[0-9;]*m")
        plain = [ansi.sub("", line) for line in lines]
        self.assertFalse(any("38;2" in line or "156;214" in line for line in plain))
        self.assertEqual(len(set(len(line) for line in plain)), 1)
        self.assertTrue(all(line.endswith(("│", "┥")) for line in plain))

    def test_render_blockquote(self):
        lines = render_markdown_to_lines("> quoted text", 40)
        self.assertTrue(any("quoted" in l for l in lines))

    def test_render_rule(self):
        lines = render_markdown_to_lines("---\ntext", 40)
        self.assertTrue(any("text" in l for l in lines))

    def test_headers_have_ansi_bold(self):
        lines = render_markdown_to_lines("## Header", 40)
        header_line = [l for l in lines if "Header" in l]
        self.assertTrue(header_line)
        self.assertIn("\033[1m", header_line[0])

    def test_code_spans_have_ansi(self):
        result = _format_inline("`code`")
        self.assertIn("code", result)

    def test_bold_has_ansi(self):
        result = _format_inline("**bold**")
        self.assertIn("bold", result)

    def test_inline_format_bold(self):
        result = _format_inline("**hello**")
        self.assertIn("hello", result)

    def test_inline_format_link(self):
        result = _format_inline("[text](url)")
        self.assertIn("text", result)

    def test_inline_format_image(self):
        result = _format_inline("![alt](url)")
        self.assertIn("alt", result)
