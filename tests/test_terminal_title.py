import json
import tempfile
import unittest
from pathlib import Path

from factory.core.cli_chat_terminal import (
    TerminalTitleConfig,
    load_terminal_title_config,
    render_terminal_title,
    save_terminal_title_items,
)


class TerminalTitleTest(unittest.TestCase):
    def test_loads_ordered_valid_items_from_factory_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "terminalTitle": {
                            "items": ["model", "unknown", "project-name", "model"],
                        }
                    }
                ),
                encoding="utf-8",
            )

            config = load_terminal_title_config(settings)

        self.assertEqual(config, TerminalTitleConfig(items=("model", "project-name")))

    def test_save_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(json.dumps({"theme": "dark", "statusLine": {"items": ["model"]}}), encoding="utf-8")

            config = save_terminal_title_items(["model", "app-name", "model", "bad"], settings_path=settings)
            data = json.loads(settings.read_text(encoding="utf-8"))

        self.assertEqual(config.items, ("model", "app-name"))
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["statusLine"]["items"], ["model"])
        self.assertEqual(data["terminalTitle"]["items"], ["model", "app-name"])

    def test_render_uses_selected_order_and_omits_unavailable_items(self):
        title = render_terminal_title(
            {
                "session_id": "session-1",
                "version": "0.1.test",
                "cwd": "/tmp/project",
                "workspace": {"current_dir": "/tmp/project", "project_dir": "/tmp/project"},
                "model": {"display_name": "qwen"},
                "git": {"branch": "main", "is_clean": False},
                "context_window": {"current_context_tokens": 1200, "context_window_size": 4000},
            },
            ("model", "project-name", "git-branch", "thread-title", "context-remaining"),
            user_text="Сделай smoke\nподробности",
        )

        self.assertEqual(title, "qwen | project | main* | Сделай smoke | 2.8k left")

    def test_empty_items_keep_legacy_task_title_default(self):
        title = render_terminal_title({}, (), user_text="Сделай smoke\nподробности")

        self.assertEqual(title, "Igorek · Сделай smoke")


if __name__ == "__main__":
    unittest.main()
