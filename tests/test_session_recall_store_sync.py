import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from factory.core.schema import open_db
from factory.core.session_recall_store import SessionRecallStore


def _write_session(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in entries) + "\n", encoding="utf-8")


class SyncSessionFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env = mock.patch.dict(os.environ, {"IGOREK_SESSION_EMBEDDINGS_BASE_URL": "", "IGOREK_SESSION_EMBEDDINGS_MODEL": ""})
        self._env.start()
        self.addCleanup(self._env.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.db = str(self.dir / "recall.db")
        self.store = SessionRecallStore(db_path=self.db)
        self.store.ensure_schema()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _counts(self) -> tuple[int, int]:
        with open_db(self.db) as conn:
            entries = conn.execute("SELECT COUNT(*) FROM session_recall_entries").fetchone()[0]
            fts = conn.execute("SELECT COUNT(*) FROM session_recall_entries_fts").fetchone()[0]
        return entries, fts

    def test_empty_chunk_session_returns_zero_without_rows(self):
        path = self.dir / "chat-empty.jsonl"
        _write_session(path, [{"role": "assistant", "text": "hi", "ts": 1}])
        self.assertEqual(self.store.sync_session_file(path), 0)
        self.assertEqual(self._counts(), (0, 0))

    def test_empty_chunk_session_preserves_unrelated_entries(self):
        with open_db(self.db) as conn:
            conn.execute(
                "INSERT INTO session_recall_entries (entry_id, session_id, entry_kind, text, content_hash, "
                "source_path, source_mtime, ts, tags_json, created_at, updated_at) "
                "VALUES ('e1', 's1', 'episodic_chunk', 'x', 'h1', NULL, 0, 0, '[]', 0, 0)"
            )
            conn.execute("INSERT INTO session_recall_entries_fts (entry_id, session_id, text) VALUES ('e1', 's1', 'x')")
        path = self.dir / "chat-empty2.jsonl"
        _write_session(path, [{"role": "assistant", "text": "hi", "ts": 1}])
        self.assertEqual(self.store.sync_session_file(path), 0)
        self.assertEqual(self._counts(), (1, 1))

    def test_session_with_chunks_then_emptied_removes_rows(self):
        path = self.dir / "chat-real.jsonl"
        _write_session(path, [{"role": "user", "text": "u" * 80, "ts": 10}, {"role": "assistant", "text": "a" * 80, "ts": 11}])
        self.assertGreaterEqual(self.store.sync_session_file(path), 1)
        self.assertGreaterEqual(self._counts()[0], 1)
        _write_session(path, [{"role": "assistant", "text": "hi", "ts": 12}])
        future = time.time() + 5
        os.utime(path, (future, future))
        self.assertEqual(self.store.sync_session_file(path), 0)
        self.assertEqual(self._counts(), (0, 0))

    def test_mtime_change_without_content_change_no_duplicates(self):
        path = self.dir / "chat-idem.jsonl"
        _write_session(path, [{"role": "user", "text": "u" * 80, "ts": 10}, {"role": "assistant", "text": "a" * 80, "ts": 11}])
        self.store.sync_session_file(path)
        before = self._counts()
        future = time.time() + 5
        os.utime(path, (future, future))
        self.store.sync_session_file(path)
        self.assertEqual(self._counts(), before)


if __name__ == "__main__":
    unittest.main()
