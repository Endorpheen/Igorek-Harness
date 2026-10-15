#!/usr/bin/env python3
"""Regressions for the stalled-task watchdog.

A fresh heartbeat must take precedence over an old claim timestamp. Using
`COALESCE(started_at, updated_at, created_at)` would incorrectly requeue a long
live task: `started_at` is set at claim, whereas the heartbeat refreshes
`updated_at` while execution continues.

Invariants:
1. A task with fresh `updated_at` and old `started_at` is not requeued.
2. Stale `updated_at` requeues with `retry_count + 1`.
3. Exhausted `max_retries` moves the task to `dead_letter`.
4. Use `started_at` as a fallback only when `updated_at` is absent.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
FACTORY_ROOT = REPO_ROOT / "factory"
for candidate in (str(REPO_ROOT), str(FACTORY_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from core.watchdog import TaskWatchdog  # noqa: E402
from factory.core.schema import initialize_database  # noqa: E402

PROFILE = "cc-opencode-go"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    # Schema created in setUp; watchdog receives a no-op to prevent the test from opening
    # an extra connection and running initialization twice.
    return None


def _insert_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    status: str = "processing",
    updated_at: int | None = None,
    started_at: int | None = None,
    created_at: int | None = None,
    retry_count: int = 0,
    max_retries: int = 3,
) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO tasks (task_id, parent_id, stage, status, profile,
                           next_profiles, capabilities, input_text, input_files,
                           output_files, retry_count, max_retries,
                           created_at, updated_at, started_at)
        VALUES (?, NULL, 'main', ?, ?, '[]', '[]', 'тестовая задача', '[]',
                '[]', ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            status,
            PROFILE,
            retry_count,
            max_retries,
            created_at if created_at is not None else now,
            updated_at if updated_at is not None else now,
            started_at,
        ),
    )
    conn.commit()


class TaskWatchdogTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "factory.db")
        initialize_database(self.db_path)
        self.watchdog = TaskWatchdog(
            db_path=self.db_path,
            ensure_schema=_ensure_schema,
            interval_s=30,
            stuck_timeout_s=600,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        self.addCleanup(conn.close)
        return conn

    def _row(self, task_id: str) -> tuple:
        with self._connect() as conn:
            return conn.execute(
                "SELECT status, retry_count, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()

    def test_live_task_with_fresh_heartbeat_is_untouched(self) -> None:
        """Old `started_at` + fresh heartbeat — task is alive; no retry should be needed."""
        now = int(time.time())
        with self._connect() as conn:
            _insert_task(
                conn,
                "live-long",
                started_at=now - 3000,
                updated_at=now - 5,
                created_at=now - 3000,
            )

        self.watchdog.run_once()

        row = self._row("live-long")
        self.assertEqual(row[0], "processing", "живая задача не должна уходить в queued")
        self.assertEqual(row[1], 0)

    def test_stale_task_is_requeued(self) -> None:
        now = int(time.time())
        with self._connect() as conn:
            _insert_task(
                conn,
                "stale-task",
                started_at=now - 3000,
                updated_at=now - 3000,
                created_at=now - 3000,
            )

        self.watchdog.run_once()

        row = self._row("stale-task")
        self.assertEqual(row[0], "queued")
        self.assertEqual(row[1], 1)
        self.assertIn("watchdog", (row[2] or "").lower())

    def test_stale_task_with_exhausted_retries_goes_dead_letter(self) -> None:
        now = int(time.time())
        with self._connect() as conn:
            _insert_task(
                conn,
                "stale-exhausted",
                updated_at=now - 3000,
                retry_count=3,
                max_retries=3,
            )

        self.watchdog.run_once()

        row = self._row("stale-exhausted")
        self.assertEqual(row[0], "dead_letter")
        self.assertEqual(row[1], 3)

    def test_started_at_is_fallback_when_updated_at_is_empty(self) -> None:
        """If the heartbeat field is absent, `started_at` is still used as an indicator."""
        now = int(time.time())
        with self._connect() as conn:
            _insert_task(conn, "no-heartbeat", started_at=now - 3000)
            conn.execute(
                "UPDATE tasks SET updated_at = NULL WHERE task_id = 'no-heartbeat'"
            )
            conn.commit()

        self.watchdog.run_once()

        row = self._row("no-heartbeat")
        self.assertEqual(row[0], "queued")


if __name__ == "__main__":
    unittest.main()
