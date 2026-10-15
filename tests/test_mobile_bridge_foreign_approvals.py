import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from factory.core.mobile_bridge.service import MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database


def _blocked_tools() -> str:
    return json.dumps(
        [
            {
                "tool_use_id": "call_foreign_1",
                "tool": "Edit",
                "target": "/tmp/target.py",
                "input": {},
                "signature": "sig",
                "reason": "тест",
            }
        ]
    )


class ForeignApprovalFilterTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())
        self.service = MobileService(profile="cc-opencode-go", workdir=str(self._tmp.name))

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _seed_blocked_task(self, task_id: str, session_id: str, platform: str | None) -> None:
        metadata: dict = {"session": session_id, "source": "test"}
        if platform is not None:
            metadata["platform"] = platform
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "INSERT INTO tasks (task_id, parent_id, stage, status, profile, input_text, metadata,"
                " created_at, updated_at, approval_blocked_tools) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    None,
                    "main",
                    "fail",
                    "cc-opencode-go",
                    "тест",
                    json.dumps(metadata),
                    1_700_000_000,
                    1_700_000_000,
                    _blocked_tools(),
                ),
            )
            conn.commit()

    def _approval_sessions(self) -> set[str]:
        return {str(item.get("session_id") or "") for item in self.service.list_approvals()}

    def test_telegram_session_approval_is_not_exposed_to_mobile(self):
        self._seed_blocked_task("task-telegram", "telegram-chat-42424242", "telegram")
        self.assertNotIn("telegram-chat-42424242", self._approval_sessions())

    def test_telegram_platform_task_without_session_is_not_exposed(self):
        self._seed_blocked_task("task-telegram-nosession", "chat-20260917-120000-tg00", "telegram")
        self.assertNotIn("chat-20260917-120000-tg00", self._approval_sessions())

    def test_mobile_session_approval_stays_exposed(self):
        self._seed_blocked_task("task-mobile", "chat-20260917-120000-abc123", "android")
        self.assertIn("chat-20260917-120000-abc123", self._approval_sessions())

    def test_cli_session_approval_stays_exposed(self):
        self._seed_blocked_task("task-cli", "chat-20260912-214943-e93ebd", None)
        self.assertIn("chat-20260912-214943-e93ebd", self._approval_sessions())
