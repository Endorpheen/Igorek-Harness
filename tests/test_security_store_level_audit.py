import os
import tempfile
import unittest
from unittest.mock import patch

from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.core.security_store import SecurityStore


class SecurityStoreLevelAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())
        self.store = SecurityStore(get_db_path())

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_log_level_change_writes_audit_event(self):
        event_id = self.store.log_level_change(
            "trusted", previous="standard", actor="mobile:dev1"
        )
        self.assertTrue(event_id)

        events = [
            item
            for item in self.store.list_events()
            if item["event_type"] == "security_level_changed"
        ]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event_id"], event_id)
        self.assertEqual(event["target"], "trusted")
        self.assertIsNone(event["task_id"])
        self.assertEqual(event["details"]["previous"], "standard")
        self.assertEqual(event["details"]["level"], "trusted")
        self.assertEqual(event["details"]["actor"], "mobile:dev1")
        self.assertIsNone(event["details"]["expires_at"])

    def test_log_level_change_records_session_and_expiry(self):
        self.store.log_level_change(
            "full",
            previous="standard",
            actor="mobile:dev1",
            session_id="s1",
            expires_at=1234567890,
        )
        event = self.store.list_events()[0]
        self.assertEqual(event["session_id"], "s1")
        self.assertEqual(event["details"]["expires_at"], 1234567890)

    def test_log_level_change_does_not_store_level_in_policy(self):
        self.store.log_level_change(
            "trusted", previous="standard", actor="mobile:dev1"
        )
        active = self.store.get_active_policy()
        self.assertNotIn("level", active["policy"])

    def test_log_level_change_rejects_unknown_level(self):
        with self.assertRaises(ValueError):
            self.store.log_level_change("bogus", previous="standard", actor="x")
        self.assertEqual(
            [
                item
                for item in self.store.list_events()
                if item["event_type"] == "security_level_changed"
            ],
            [],
        )

    def test_log_level_change_accepts_empty_previous(self):
        self.store.log_level_change("standard", previous="", actor="x")
        event = self.store.list_events()[0]
        self.assertEqual(event["details"]["previous"], "")


if __name__ == "__main__":
    unittest.main()
