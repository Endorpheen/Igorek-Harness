import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory.core.external_sessions import ExternalSessionCatalog


class ExternalSessionCatalogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.claude = self.root / "claude"
        self.codex = self.root / "codex"
        self.opencode = self.root / "opencode.db"
        self._write_claude()
        self._write_codex()
        self._write_opencode()
        self.catalog = ExternalSessionCatalog(
            claude_root=self.claude,
            codex_root=self.codex,
            opencode_db=self.opencode,
            cache_ttl_s=0,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _write_claude(self):
        project = self.claude / "projects" / "-tmp-project"
        project.mkdir(parents=True)
        path = project / "claude-1.jsonl"
        rows = [
            {
                "type": "user",
                "sessionId": "claude-1",
                "cwd": "/tmp/project",
                "timestamp": "2026-01-02T03:04:05Z",
                "message": {"role": "user", "content": "Claude question"},
            },
            {
                "type": "assistant",
                "sessionId": "claude-1",
                "cwd": "/tmp/project",
                "timestamp": "2026-01-02T03:04:06Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Claude answer"},
                        {"type": "tool_use", "name": "Bash", "input": {"token": "secret"}},
                    ],
                },
            },
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        (project / "sessions-index.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": [
                        {
                            "sessionId": "claude-1",
                            "fullPath": str(path),
                            "firstPrompt": "Claude question",
                            "messageCount": 2,
                            "created": "2026-01-02T03:04:05Z",
                            "modified": "2026-01-02T03:04:06Z",
                            "projectPath": "/tmp/project",
                            "isSidechain": False,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def _write_codex(self):
        self.codex.mkdir()
        rollout = self.codex / "sessions" / "2026" / "01" / "02" / "rollout-codex-1.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text("", encoding="utf-8")
        with sqlite3.connect(self.codex / "state_5.sqlite") as connection:
            connection.execute(
                "CREATE TABLE threads (id TEXT, name TEXT, title TEXT, cwd TEXT, "
                "created_at INTEGER, updated_at INTEGER, created_at_ms INTEGER, updated_at_ms INTEGER, "
                "model TEXT, agent_nickname TEXT, rollout_path TEXT, preview TEXT, first_user_message TEXT)"
            )
            connection.execute(
                "INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "codex-1",
                    "",
                    "Codex title",
                    "/tmp/codex",
                    100,
                    200,
                    100000,
                    200000,
                    "gpt-test",
                    "",
                    str(rollout),
                    "Codex preview",
                    "Codex question",
                ),
            )
        with sqlite3.connect(self.codex / "thread_history_1.sqlite") as connection:
            connection.execute(
                "CREATE TABLE thread_items (thread_id TEXT, rollout_ordinal INTEGER, "
                "created_at_ms INTEGER, item_json TEXT, item_type TEXT)"
            )
            items = [
                {"type": "userMessage", "id": "u1", "content": [{"type": "text", "text": "Codex question"}]},
                {"type": "agentMessage", "id": "a1", "text": "Codex answer"},
                {
                    "type": "commandExecution",
                    "id": "t1",
                    "command": "pwd",
                    "aggregatedOutput": "/tmp/codex",
                },
            ]
            for index, item in enumerate(items):
                connection.execute(
                    "INSERT INTO thread_items VALUES (?,?,?,?,?)",
                    ("codex-1", index, 100000 + index, json.dumps(item), item["type"]),
                )

    def _write_opencode(self):
        with sqlite3.connect(self.opencode) as connection:
            connection.execute(
                "CREATE TABLE session (id TEXT, title TEXT, directory TEXT, time_created INTEGER, "
                "time_updated INTEGER, model TEXT, agent TEXT)"
            )
            connection.execute(
                "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT)"
            )
            connection.execute(
                "CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT)"
            )
            connection.execute(
                "INSERT INTO session VALUES (?,?,?,?,?,?,?)",
                (
                    "open-1",
                    "OpenCode title",
                    "/tmp/open",
                    300000,
                    400000,
                    json.dumps({"providerID": "provider", "id": "model"}),
                    "agent",
                ),
            )
            connection.execute(
                "INSERT INTO message VALUES (?,?,?,?)",
                ("m1", "open-1", 300000, json.dumps({"role": "user"})),
            )
            connection.execute(
                "INSERT INTO part VALUES (?,?,?,?,?)",
                ("p1", "m1", "open-1", 300000, json.dumps({"type": "text", "text": "Open question"})),
            )

    def test_scan_unifies_all_sources(self):
        payload = self.catalog.list_sessions(refresh=True)

        self.assertEqual(payload["counts"], {"claude": 1, "opencode": 1, "codex": 1})
        self.assertEqual(payload["total"], 3)
        by_source = {item["source"]: item for item in payload["sessions"]}
        self.assertEqual(by_source["claude"]["cwd"], "/tmp/project")
        self.assertEqual(by_source["codex"]["model"], "gpt-test")
        self.assertEqual(by_source["codex"]["message_count"], 2)
        self.assertEqual(by_source["opencode"]["model"], "provider/model")
        self.assertTrue(all(item["native_resume"] for item in payload["sessions"]))

    def test_read_normalizes_messages_and_redacts_tool_secrets(self):
        claude = self.catalog.get_session("claude", "claude-1")
        codex = self.catalog.get_session("codex", "codex-1")
        opencode = self.catalog.get_session("opencode", "open-1")

        self.assertEqual([item["role"] for item in claude["messages"]], ["user", "assistant", "tool"])
        self.assertNotIn("secret", claude["messages"][-1]["text"])
        self.assertEqual([item["text"] for item in codex["messages"][:2]], ["Codex question", "Codex answer"])
        self.assertEqual(opencode["messages"][0]["text"], "Open question")

    def test_import_keeps_full_history_and_structured_tools(self):
        record, messages, skipped = self.catalog.import_messages("codex", "codex-1")

        self.assertEqual(record.title, "Codex title")
        self.assertEqual([item["role"] for item in messages], ["user", "assistant", "tool"])
        self.assertEqual(messages[2]["tool_use_id"], "t1")
        self.assertEqual(messages[2]["tool_input"], {"command": "pwd"})
        self.assertEqual(messages[2]["tool_output"], "/tmp/codex")
        self.assertEqual([item["order"] for item in messages], [0, 1, 2])
        self.assertEqual(len({item["history_id"] for item in messages}), 3)
        self.assertEqual(skipped, 0)

    def test_import_does_not_drop_older_records_or_long_text(self):
        long_text = "a" * 60000
        self.assertEqual(self.catalog._message("user", long_text)["text"], long_text)
        messages = [{"role": "user", "text": str(i)} for i in range(450)]
        from unittest.mock import patch
        with patch.object(self.catalog, "_messages", return_value=messages):
            _, imported, skipped = self.catalog.import_messages("codex", "codex-1", max_messages=2, max_chars=10)
        self.assertEqual(len(imported), 450)
        self.assertEqual(skipped, 0)

    def test_unknown_source_is_rejected(self):
        with self.assertRaises(ValueError):
            self.catalog.list_sessions(source="unknown")

    def test_unknown_session_is_rejected(self):
        with self.assertRaises(KeyError):
            self.catalog.get_session("claude", "missing")


if __name__ == "__main__":
    unittest.main()
