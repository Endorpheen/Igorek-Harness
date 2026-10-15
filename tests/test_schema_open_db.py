import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory.core.schema import open_db


def _open_fd_count() -> int:
    # Linux-only, matches the production failure mode exactly: EMFILE is an
    # open-file-descriptor limit, so the leak check must count FDs, not objects.
    return len(os.listdir("/proc/self/fd"))


class OpenDbTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "test.db")
        with open_db(self.db_path) as conn:
            conn.execute("CREATE TABLE t (v INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_connection_closed_after_block(self):
        before = _open_fd_count()
        with open_db(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT v FROM t").fetchone()[0], 1)
        self.assertEqual(_open_fd_count(), before)

    def test_commits_on_success(self):
        with open_db(self.db_path) as conn:
            conn.execute("INSERT INTO t VALUES (2)")
        with open_db(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        self.assertEqual(total, 2)

    def test_rolls_back_on_exception_and_closes(self):
        before = _open_fd_count()
        with self.assertRaises(RuntimeError):
            with open_db(self.db_path) as conn:
                conn.execute("INSERT INTO t VALUES (3)")
                raise RuntimeError("boom")
        self.assertEqual(_open_fd_count(), before)
        with open_db(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        self.assertEqual(total, 1)  # failed insert must be rolled back

    def test_session_recall_store_leaves_no_open_connections(self):
        # SessionSearch runs on every cc-backend round: this is the hottest
        # historical leak path (one leaked db+wal FD pair per call).
        from factory.core.session_recall_store import SessionRecallStore

        store = SessionRecallStore(db_path=str(Path(self._tmp.name) / "recall.db"))
        store.ensure_schema()  # warm-up: any lazy imports resolve here
        before = _open_fd_count()
        for _ in range(3):
            store.ensure_schema()
        self.assertEqual(_open_fd_count(), before)


if __name__ == "__main__":
    unittest.main()
