import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from factory.core import security_settings as ss
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database


class SecuritySettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def _write_raw(self, key: str, raw: str) -> None:
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, raw, int(time.time())),
            )

    def test_default_resolves_to_baseline_standard(self):
        result = ss.resolve_security_level()
        self.assertEqual(result["level"], "standard")
        self.assertEqual(result["baseline"], "standard")
        self.assertEqual(result["source"], "baseline")
        self.assertIsNone(result["expires_at"])
        self.assertFalse(result["is_weakened"])

    def test_global_level_applies(self):
        ss.set_security_level("strict")
        result = ss.resolve_security_level()
        self.assertEqual(result["level"], "strict")
        self.assertEqual(result["source"], "global")
        self.assertFalse(result["is_weakened"])

    def test_weakening_requires_bounded_ttl(self):
        with self.assertRaises(ValueError):
            ss.set_security_level("full", ttl_s=7200)
        with self.assertRaises(ValueError):
            ss.set_security_level("full", ttl_s=0)
        with self.assertRaises(ValueError):
            ss.set_security_level("full", ttl_s=-5)
        with self.assertRaises(ValueError):
            ss.set_security_level("trusted", ttl_s=90000)
        # Bounds are permitted.
        ss.set_security_level("full", ttl_s=3600)
        ss.set_security_level("trusted", ttl_s=86400)

    def test_weakening_without_ttl_uses_default(self):
        before = int(time.time())
        record = ss.set_security_level("full")
        self.assertIsNotNone(record["expires_at"])
        self.assertGreaterEqual(record["expires_at"], before + ss.DEFAULT_TTL_S)
        self.assertLessEqual(record["expires_at"], int(time.time()) + ss.DEFAULT_TTL_S)
        self.assertEqual(ss.resolve_security_level()["level"], "full")

    def test_expires_at_is_not_an_api_parameter(self):
        with self.assertRaises(TypeError):
            ss.set_security_level("full", expires_at=None)
        with self.assertRaises(TypeError):
            ss.set_security_level("full", expires_at=int(time.time()) + 60)

    def test_set_unknown_level_rejected(self):
        with self.assertRaises(ValueError):
            ss.set_security_level("root")
        with self.assertRaises(ValueError):
            ss.set_security_level("user-read")

    def test_raw_weakening_without_expiry_falls_through(self):
        ss.set_security_level("full", ttl_s=60)
        self._write_raw("security_level", json.dumps({"level": "full"}))
        result = ss.resolve_security_level()
        self.assertEqual(result["level"], "standard")
        self.assertEqual(result["source"], "baseline")

    def test_raw_expired_weakening_falls_through(self):
        self._write_raw(
            "security_level",
            json.dumps({"level": "trusted", "expires_at": int(time.time()) - 10}),
        )
        result = ss.resolve_security_level()
        self.assertEqual(result["level"], "standard")
        self.assertEqual(result["source"], "baseline")

    def test_raw_non_numeric_expiry_falls_through(self):
        self._write_raw(
            "security_level",
            json.dumps({"level": "full", "expires_at": "soon"}),
        )
        self.assertEqual(ss.resolve_security_level()["level"], "standard")

    def test_raw_unknown_level_falls_through_without_error(self):
        self._write_raw("security_level", json.dumps({"level": "root"}))
        self.assertEqual(ss.resolve_security_level()["level"], "standard")

    def test_raw_broken_json_falls_through(self):
        self._write_raw("security_level", "{not json")
        self.assertEqual(ss.resolve_security_level()["level"], "standard")

    def test_session_override_beats_global(self):
        ss.set_security_level("standard")
        ss.set_security_level("full", session_id="s1", ttl_s=60)
        session_result = ss.resolve_security_level(session_id="s1")
        self.assertEqual(session_result["level"], "full")
        self.assertEqual(session_result["source"], "session")
        self.assertTrue(session_result["is_weakened"])
        self.assertEqual(ss.resolve_security_level()["level"], "standard")

    def test_expired_session_falls_back_to_global(self):
        ss.set_security_level("strict")
        ss.set_security_level("full", session_id="s1", ttl_s=60)
        self._write_raw(
            "security_level:session:s1",
            json.dumps({"level": "full", "expires_at": int(time.time()) - 1}),
        )
        result = ss.resolve_security_level(session_id="s1")
        self.assertEqual(result["level"], "strict")
        self.assertEqual(result["source"], "global")

    def test_invalid_session_falls_back_to_global(self):
        ss.set_security_level("standard")
        self._write_raw("security_level:session:s1", json.dumps({"level": "full"}))
        result = ss.resolve_security_level(session_id="s1")
        self.assertEqual(result["level"], "standard")
        self.assertEqual(result["source"], "global")

    def test_expired_global_falls_back_to_baseline(self):
        ss.set_security_level("full", ttl_s=60)
        self._write_raw(
            "security_level",
            json.dumps({"level": "full", "expires_at": int(time.time()) - 1}),
        )
        result = ss.resolve_security_level()
        self.assertEqual(result["level"], "standard")
        self.assertEqual(result["source"], "baseline")

    def test_when_no_level_user_read_is_preserved(self):
        result = ss.resolve_security_level(when_no_level="user-read")
        self.assertEqual(result["level"], "user-read")
        self.assertEqual(result["baseline"], "user-read")
        self.assertEqual(result["source"], "baseline")
        self.assertFalse(result["is_weakened"])

    def test_when_no_level_full_dangerous_is_standard(self):
        result = ss.resolve_security_level(when_no_level="full-dangerous")
        self.assertEqual(result["level"], "standard")
        self.assertEqual(result["baseline"], "standard")
        self.assertFalse(result["is_weakened"])

    def test_when_no_level_unknown_is_standard(self):
        self.assertEqual(
            ss.resolve_security_level(when_no_level="nonsense")["level"], "standard"
        )

    def test_clear_session_level(self):
        ss.set_security_level("standard")
        ss.set_security_level("full", session_id="s1", ttl_s=60)
        ss.clear_session_level("s1")
        result = ss.resolve_security_level(session_id="s1")
        self.assertEqual(result["level"], "standard")
        self.assertEqual(result["source"], "global")

    def test_get_helpers_report_valid_records_only(self):
        self.assertIsNone(ss.get_global_level())
        self.assertIsNone(ss.get_session_level(None))

        ss.set_security_level("trusted", ttl_s=60)
        self.assertEqual(ss.get_global_level()["level"], "trusted")

        ss.set_security_level("full", session_id="s1", ttl_s=60)
        self.assertEqual(ss.get_session_level("s1")["level"], "full")

        self._write_raw("security_level", json.dumps({"level": "full"}))
        self.assertIsNone(ss.get_global_level())

    def test_strengthening_may_be_permanent(self):
        record = ss.set_security_level("strict")
        self.assertIsNone(record["expires_at"])
        result = ss.resolve_security_level()
        self.assertEqual(result["level"], "strict")
        self.assertIsNone(result["expires_at"])


if __name__ == "__main__":
    unittest.main()
