import json
import tempfile
import unittest
from pathlib import Path

from factory.core.tui.cells.history_cell import DiffCell
from factory.core.tui.markdown_render import render_markdown_to_lines
from factory.core.tui.theme import (
    DEFAULT_THEME_NAME,
    current_theme,
    load_theme_name,
    preview_theme,
    save_theme_name,
)


class ThemeTest(unittest.TestCase):
    def test_save_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(json.dumps({"statusLine": {"items": ["model"]}}), encoding="utf-8")

            theme = save_theme_name("light", settings_path=settings)
            data = json.loads(settings.read_text(encoding="utf-8"))

        self.assertEqual(theme.name, "light")
        self.assertEqual(data["theme"], "light")
        self.assertEqual(data["statusLine"]["items"], ["model"])

    def test_reset_removes_override_and_restores_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            save_theme_name("monokai", settings_path=settings)

            theme = save_theme_name(None, settings_path=settings)
            data = json.loads(settings.read_text(encoding="utf-8"))

        self.assertEqual(theme.name, DEFAULT_THEME_NAME)
        self.assertEqual(load_theme_name(settings), DEFAULT_THEME_NAME)
        self.assertNotIn("theme", data)

    def test_preview_theme_changes_markdown_and_diff_palette_then_restores(self):
        before = current_theme().name

        with preview_theme("light"):
            markdown = "\n".join(render_markdown_to_lines("```python\nx = 1\n```", 40))
            diff = "\n".join(DiffCell("old", "new", "preview.py").display_lines(72))
            active = current_theme().name

        self.assertEqual(active, "light")
        self.assertIn("x = 1", markdown)
        self.assertIn("\033[48;5;194m\033[38;5;28m", diff)
        self.assertEqual(current_theme().name, before)

    def test_unknown_saved_theme_uses_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(json.dumps({"theme": "missing"}), encoding="utf-8")

            name = load_theme_name(settings)

        self.assertEqual(name, DEFAULT_THEME_NAME)


if __name__ == "__main__":
    unittest.main()
