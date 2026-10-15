import json
import os
import re
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from factory.core.schema import initialize_database
from factory.core.status_line import (
    StatusLineConfig,
    build_status_line_input,
    execute_status_line_command,
    load_status_line_config,
    render_configured_status_line,
    render_builtin_status_line,
    save_status_line_items,
)


class StatusLineTest(unittest.TestCase):
    def test_loads_command_config_from_factory_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "statusLine": {
                            "type": "command",
                            "command": "printf ok",
                            "padding": 2,
                        }
                    }
                ),
                encoding="utf-8",
            )

            config = load_status_line_config(settings)

        self.assertEqual(config, StatusLineConfig(type="command", command="printf ok", padding=2))

    def test_loads_builtin_items_from_factory_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "statusLine": {
                            "type": "builtin",
                            "items": [
                                "model",
                                "unknown",
                                "profile",
                                "model",
                                "context-remaining",
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )

            config = load_status_line_config(settings)

        self.assertEqual(config.items, ("model", "profile", "context-remaining"))

    def test_save_status_line_items_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            settings.write_text(
                json.dumps({"theme": "dark", "statusLine": {"type": "command"}}),
                encoding="utf-8",
            )

            config = save_status_line_items(
                ["model", "profile", "model", "bad"],
                settings_path=settings,
            )
            data = json.loads(settings.read_text(encoding="utf-8"))

        self.assertEqual(config.items, ("model", "profile"))
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["statusLine"]["type"], "builtin")
        self.assertEqual(data["statusLine"]["items"], ["model", "profile"])

    def test_render_configured_status_line_uses_selected_order(self):
        status_input = {
            "backend": {"profile": "cc-openrouter"},
            "model": {"display_name": "qwen"},
            "cwd": "/tmp/project",
            "workspace": {
                "current_dir": "/tmp/project",
                "project_dir": "/tmp",
            },
            "git": {"branch": "main", "is_clean": True},
            "context_window": {
                "current_context_tokens": 1200,
                "context_window_size": 4000,
            },
            "cost": {"total_cost_usd": 0.0},
            "version": "0.1.test",
            "session_id": "session-1",
        }

        text = render_configured_status_line(
            status_input,
            ("model", "profile", "context-remaining", "cost", "git-branch"),
        )

        self.assertEqual(re.sub(r"\033\[[0-9;]*m", "", text), "qwen · cc-openrouter · 2.8k left · main")

    def test_executes_status_line_command_with_json_stdin(self):
        config = StatusLineConfig(
            type="command",
            command=(
                "python3 -c 'import json,sys; "
                "data=json.load(sys.stdin); "
                "print(data[\"workspace\"][\"current_dir\"])'"
            ),
        )

        text = execute_status_line_command(
            config,
            {"workspace": {"current_dir": "/tmp/project"}},
            timeout_s=2,
        )

        self.assertEqual(text, "/tmp/project")

    def test_builtin_status_line_uses_git_context_and_token_counts(self):
        status_input = {
            "backend": {"profile": "cc-local"},
            "model": {"display_name": "gemma"},
            "cwd": "/tmp/project",
            "workspace": {"current_dir": "/tmp/project"},
            "git": {"branch": "feature", "is_clean": False},
            "context_window": {
                "current_usage": {"input_tokens": 1000, "output_tokens": 500},
                "context_window_size": 4000,
            },
        }

        text = render_builtin_status_line(status_input)

        self.assertIn("cc-local", text)
        self.assertIn("gemma", text)
        self.assertIn("feature*", text)
        self.assertIn("ctx 1.5k/4.0k", text)
        self.assertIn("38% used", text)
        self.assertIn("2.5k left", text)
        self.assertIn("/tmp/project", text)

    def test_builtin_status_line_shows_session_cost_when_present(self):
        status_input = {
            "backend": {"profile": "cc-openrouter", "provider": "openrouter"},
            "model": {"display_name": "qwen/qwen3.6-plus"},
            "cwd": "/tmp/project",
            "workspace": {"current_dir": "/tmp/project"},
            "git": {"branch": "main", "is_clean": True},
            "cost": {"total_cost_usd": 0.024},
            "context_window": {
                "current_usage": {"input_tokens": 10_000, "output_tokens": 1_000},
                "context_window_size": 1_000_000,
            },
        }

        text = render_builtin_status_line(status_input)

        self.assertIn("$0.02", text)

    def test_builtin_status_line_uses_current_context_not_session_total(self):
        status_input = {
            "backend": {"profile": "cc-openrouter", "provider": "openrouter"},
            "model": {"display_name": "tencent/hy3-preview:free"},
            "cwd": "/tmp/project",
            "workspace": {"current_dir": "/tmp/project"},
            "git": {"branch": "main", "is_clean": True},
            "context_window": {
                "current_context_tokens": 14_228,
                "session_total_tokens": 60_968,
                "context_window_size": 262_144,
            },
        }

        text = render_builtin_status_line(status_input)

        self.assertIn("ctx 14.2k/262.1k", text)
        self.assertIn("5% used", text)
        self.assertNotIn("ctx 61.0k/262.1k", text)

    def test_status_line_input_reads_usage_for_current_task_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_home = os.environ.get("IGOREK_FACTORY_HOME")
            os.environ["IGOREK_FACTORY_HOME"] = tmp
            try:
                db_path = initialize_database(str(Path(tmp) / "database" / "factory.db"))
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        """
                        INSERT INTO tasks (
                            task_id, status, profile, input_text, output_text,
                            usage_prompt, usage_completion, cost_usd, metadata,
                            created_at, completed_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "task-1",
                            "ok",
                            "cc-local",
                            "in",
                            "out",
                            10,
                            5,
                            0.012,
                            json.dumps(
                                {
                                    "usage_ext": {
                                        "total_tokens": 1234,
                                        "model_context_window": 2000,
                                        "input_tokens": 1000,
                                        "output_tokens": 234,
                                    }
                                }
                            ),
                            int(time.time()),
                            int(time.time()),
                        ),
                    )
                    conn.commit()

                status_input = build_status_line_input(
                    session_name="default",
                    session_path=Path(tmp) / "chat_sessions" / "default.jsonl",
                    profile="cc-local",
                    profile_config={"backend": "cc-backend"},
                    model="gemma",
                    cwd=tmp,
                    version="0.1.test",
                    task_ids=["task-1"],
                )
            finally:
                if old_home is None:
                    os.environ.pop("IGOREK_FACTORY_HOME", None)
                else:
                    os.environ["IGOREK_FACTORY_HOME"] = old_home

        context = status_input["context_window"]
        self.assertEqual(context["context_window_size"], 2000)
        self.assertEqual(context["used_percentage"], 62)
        self.assertEqual(context["remaining_percentage"], 38)
        self.assertEqual(context["current_usage"]["input_tokens"], 1000)
        self.assertAlmostEqual(status_input["cost"]["total_cost_usd"], 0.012)

    def test_status_line_input_prefers_usage_ext_current_usage_for_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_home = os.environ.get("IGOREK_FACTORY_HOME")
            os.environ["IGOREK_FACTORY_HOME"] = tmp
            try:
                db_path = initialize_database(str(Path(tmp) / "database" / "factory.db"))
                now = int(time.time())
                with sqlite3.connect(db_path) as conn:
                    for task_id, prompt_tokens, current_usage in (
                        ("task-1", 39_770, 16_718),
                        ("task-2", 18_821, 14_228),
                    ):
                        conn.execute(
                            """
                            INSERT INTO tasks (
                                task_id, status, profile, input_text, output_text,
                                usage_prompt, usage_completion, cost_usd, metadata,
                                created_at, completed_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                task_id,
                                "ok",
                                "cc-openrouter",
                                "in",
                                "out",
                                prompt_tokens,
                                500,
                                0.0,
                                json.dumps(
                                    {
                                        "usage_ext": {
                                            "current_usage": current_usage,
                                            "model_context_window": 262_144,
                                        }
                                    }
                                ),
                                now,
                                now + (1 if task_id == "task-2" else 0),
                            ),
                        )
                    conn.commit()

                status_input = build_status_line_input(
                    session_name="default",
                    session_path=Path(tmp) / "chat_sessions" / "default.jsonl",
                    profile="cc-openrouter",
                    profile_config={"backend": "cc-backend", "provider": "openrouter"},
                    model="tencent/hy3-preview:free",
                    cwd=tmp,
                    version="0.1.test",
                    task_ids=["task-1", "task-2"],
                )
            finally:
                if old_home is None:
                    os.environ.pop("IGOREK_FACTORY_HOME", None)
                else:
                    os.environ["IGOREK_FACTORY_HOME"] = old_home

        context = status_input["context_window"]
        self.assertEqual(context["current_context_tokens"], 14_228)
        self.assertEqual(context["session_total_tokens"], 59_591)
        self.assertEqual(context["used_percentage"], 5)


if __name__ == "__main__":
    unittest.main()
