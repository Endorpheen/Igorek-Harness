import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from factory.core import worker as subject


class TestRecentRolloutContracts(unittest.TestCase):
    def test_only_fresh_rollout_is_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "old.jsonl"
            fresh = Path(tmp) / "fresh.jsonl"
            old.write_text("old", encoding="utf-8")
            fresh.write_text("fresh", encoding="utf-8")
            os.utime(old, (10, 10))
            os.utime(fresh, (100, 100))
            with unittest.mock.patch.object(
                subject, "_codex_rollout_files", return_value=[old, fresh]
            ):
                result = subject._recent_codex_rollout_text(
                    codex_home=tmp, started_at=99
                )
        self.assertEqual(result, "fresh")

    def test_missing_file_does_not_break_reading_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.jsonl"
            fresh = Path(tmp) / "fresh.jsonl"
            fresh.write_text("valid", encoding="utf-8")
            os.utime(fresh, (100, 100))
            with unittest.mock.patch.object(
                subject, "_codex_rollout_files", return_value=[missing, fresh]
            ):
                result = subject._recent_codex_rollout_text(
                    codex_home=tmp, started_at=99
                )
        self.assertEqual(result, "valid")


if __name__ == "__main__":
    unittest.main()
