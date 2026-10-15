#!/usr/bin/env python3
"""Regressions for worker recovery after stalled tasks.

A live task must not disappear when another profile's idle worker performs
cleanup. Without claim/heartbeat updates, an old creation time can make live
`processing` work appear stale. `DELETE FROM tasks` then hides it from
`TraceJournaler._db_tasks`, interrupts `mobile_events`, removes the client card
and prevents cancellation while execution continues.

Invariants:
1. Cleanup does not affect another profile's tasks.
2. Preserve the row and set `dead_letter`, allowing a final `task.status` event.
3. Heartbeats refresh `updated_at` so long live tasks are not classified stale.
4. `claim_task` refreshes `updated_at` instead of retaining creation time.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core import worker as worker_module  # noqa: E402
from factory.core.runtime import get_db_path  # noqa: E402
from factory.core.schema import initialize_database  # noqa: E402
from factory.core.worker import Worker  # noqa: E402

OWN_PROFILE = "cc-wally"
OTHER_PROFILE = "cc-opencode-go"


def _insert_task(
    conn: sqlite3.Connection,
    task_id: str,
    profile: str,
    *,
    status: str = "processing",
    updated_at: int | None = None,
    created_at: int | None = None,
    input_text: str = "тестовая задача",
) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO tasks (task_id, parent_id, stage, status, profile,
                           next_profiles, capabilities, input_text, input_files,
                           output_files, retry_count, max_retries,
                           created_at, updated_at)
        VALUES (?, NULL, 'main', ?, ?, '[]', '[]', ?, '[]', '[]', 0, 3, ?, ?)
        """,
        (
            task_id,
            status,
            profile,
            input_text,
            created_at if created_at is not None else now,
            updated_at if updated_at is not None else now,
        ),
    )
    conn.commit()


class StuckTaskRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "factory.db")
        initialize_database(self.db_path)

        patcher = patch.object(worker_module, "DB_PATH", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Lightweight worker: tests require only `profile`, `# heartbeat_interval`, and recovery methods.
        self.worker = Worker.__new__(Worker)
        self.worker.profile = OWN_PROFILE
        self.worker.heartbeat_interval = 30

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        self.addCleanup(conn.close)
        return conn

    def _status(self, task_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return None if row is None else row[0]

    # --- 1. Do not modify a foreign profile --

    def test_stuck_task_of_other_profile_is_untouched(self) -> None:
        with self._connect() as conn:
            _insert_task(
                conn,
                "foreign-stuck",
                OTHER_PROFILE,
                updated_at=int(time.time()) - 400,
            )

        self.worker.cleanup_stuck_tasks(timeout_seconds=300)

        self.assertEqual(
            self._status("foreign-stuck"),
            "processing",
            "воркер не имеет права убирать задачи чужого профиля",
        )

    # --- 2. Own stuck task: dead_letter, record saved --

    def test_stuck_task_of_own_profile_is_marked_dead_letter(self) -> None:
        with self._connect() as conn:
            _insert_task(
                conn,
                "own-stuck",
                OWN_PROFILE,
                updated_at=int(time.time()) - 400,
            )

        self.worker.cleanup_stuck_tasks(timeout_seconds=300)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, error_message FROM tasks WHERE task_id = 'own-stuck'"
            ).fetchone()

        self.assertIsNotNone(row, "строка задачи не должна удаляться из БД")
        self.assertEqual(row[0], "dead_letter")
        self.assertIn("no heartbeat", row[1] or "")

    def test_fresh_processing_task_is_untouched(self) -> None:
        with self._connect() as conn:
            _insert_task(conn, "own-fresh", OWN_PROFILE)

        self.worker.cleanup_stuck_tasks(timeout_seconds=300)

        self.assertEqual(self._status("own-fresh"), "processing")

    # --- 3. Heartbeat protects a live, long-running task --

    def test_touch_task_refreshes_updated_at(self) -> None:
        old = int(time.time()) - 400
        with self._connect() as conn:
            _insert_task(conn, "live-task", OWN_PROFILE, updated_at=old)

        self.assertTrue(self.worker._touch_task("live-task"))

        with self._connect() as conn:
            row = conn.execute(
                "SELECT updated_at FROM tasks WHERE task_id = 'live-task'"
            ).fetchone()
        self.assertGreater(row[0], old)

    def test_touch_task_returns_false_for_missing_task(self) -> None:
        self.assertFalse(self.worker._touch_task("does-not-exist"))

    def test_heartbeat_keeps_long_running_task_alive(self) -> None:
        with self._connect() as conn:
            _insert_task(
                conn,
                "live-long",
                OWN_PROFILE,
                updated_at=int(time.time()) - 400,
            )

        # One heartbeat tick during execution — similar to
        # _execute_task_with_heartbeat.
        self.assertTrue(self.worker._touch_task("live-long"))
        self.worker.cleanup_stuck_tasks(timeout_seconds=300)

        self.assertEqual(
            self._status("live-long"),
            "processing",
            "живая задача с heartbeat не должна помечаться как зависшая",
        )

    # --- 4. `claim` updates `updated_at` --

    def test_claim_task_refreshes_updated_at(self) -> None:
        old = int(time.time()) - 1000
        with self._connect() as conn:
            _insert_task(
                conn,
                "queued-task",
                OWN_PROFILE,
                status="queued",
                updated_at=old,
                created_at=old,
            )

            claimed = self.worker.claim_task(conn)
            self.assertIsNotNone(claimed)

            row = conn.execute(
                "SELECT status, updated_at FROM tasks WHERE task_id = 'queued-task'"
            ).fetchone()

        self.assertEqual(row[0], "processing")
        self.assertGreater(
            row[1],
            old,
            "claim_task обязан обновлять updated_at, иначе долгая задача "
            "сразу выглядит зависшей",
        )

    # --- 5. `cancelling` without heartbeat — terminal deadlock --

    def test_stuck_cancelling_task_of_own_profile_is_finalized(self) -> None:
        """`cancelling` without heartbeat used to persist indefinitely: no one selected it.

        `claim_task` only takes `queued` tasks, while cleanup only checked `processing`.
        The mobile bridge, however, considered `cancelling` an active session task,
        so the client forever displayed 'stopping.' The Stop action did not trigger,
        and new messages hung in 'queue.' Now, the task finalizes in `fail` + `cancelled by user`.
        """
        with self._connect() as conn:
            _insert_task(
                conn,
                "own-cancelling",
                OWN_PROFILE,
                status="cancelling",
                updated_at=int(time.time()) - 400,
            )

        self.worker.cleanup_stuck_tasks(timeout_seconds=300)

        with self._connect() as conn:
            row = conn.execute(
                "SELECT status, error_message FROM tasks WHERE task_id = 'own-cancelling'"
            ).fetchone()

        self.assertIsNotNone(row, "строка задачи не должна удаляться из БД")
        self.assertEqual(row[0], "fail")
        self.assertIn("cancelled", (row[1] or "").lower())

    def test_live_cancelling_task_is_untouched(self) -> None:
        """A live cancellable task is protected by heartbeat and does not finalize.

        The user clicked Stop, but the executor was still mid-round. The cleaner
        cannot close the task before the executor—otherwise, it wouldn’t be able
        to append a correct final status.
        """
        with self._connect() as conn:
            _insert_task(
                conn,
                "live-cancelling",
                OWN_PROFILE,
                status="cancelling",
                updated_at=int(time.time()) - 400,
            )

        self.assertTrue(self.worker._touch_task("live-cancelling"))
        self.worker.cleanup_stuck_tasks(timeout_seconds=300)

        self.assertEqual(self._status("live-cancelling"), "cancelling")

    def test_touch_task_refreshes_updated_at_for_cancelling(self) -> None:
        old = int(time.time()) - 400
        with self._connect() as conn:
            _insert_task(
                conn,
                "cancel-live",
                OWN_PROFILE,
                status="cancelling",
                updated_at=old,
            )

        self.assertTrue(self.worker._touch_task("cancel-live"))

        with self._connect() as conn:
            row = conn.execute(
                "SELECT updated_at FROM tasks WHERE task_id = 'cancel-live'"
            ).fetchone()
        self.assertGreater(row[0], old)

    def test_stuck_cancelling_task_of_other_profile_is_untouched(self) -> None:
        with self._connect() as conn:
            _insert_task(
                conn,
                "foreign-cancelling",
                OTHER_PROFILE,
                status="cancelling",
                updated_at=int(time.time()) - 400,
            )

        self.worker.cleanup_stuck_tasks(timeout_seconds=300)

        self.assertEqual(self._status("foreign-cancelling"), "cancelling")


class TaskShouldStopTest(unittest.TestCase):
    """`task_should_stop` — single stopping rule for the executor.

    Covers the second symptom of the incident: a task removed from `tasks`
    continued running (as a zombie) and held the profile queue, causing
    new client tasks to hang in `queued`.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        env.start()
        self.addCleanup(env.stop)
        initialize_database(get_db_path())

    def _insert(self, task_id: str, status: str) -> None:
        with sqlite3.connect(get_db_path()) as conn:
            _insert_task(conn, task_id, OWN_PROFILE, status=status)

    def test_empty_task_id_is_not_tracked(self) -> None:
        """Subtasks with `delegate_*` and one-off agents do not have task lines—we do not modify them."""
        from factory.core.cc_backend_executor import task_should_stop

        self.assertFalse(task_should_stop(""))
        self.assertFalse(task_should_stop(None))

    def test_missing_task_row_stops_execution(self) -> None:
        from factory.core.cc_backend_executor import task_should_stop

        self.assertTrue(
            task_should_stop("gone-task"),
            "удалённую задачу продолжать нельзя: результат некуда писать, "
            "а очередь профиля занята",
        )

    def test_cancelling_task_stops_execution(self) -> None:
        from factory.core.cc_backend_executor import task_should_stop

        self._insert("cancel-me", "cancelling")
        self.assertTrue(task_should_stop("cancel-me"))

    def test_processing_task_does_not_stop(self) -> None:
        from factory.core.cc_backend_executor import task_should_stop

        self._insert("work-me", "processing")
        self.assertFalse(task_should_stop("work-me"))

    def test_guest_turn_does_not_stop_on_a_missing_local_row(self) -> None:
        """In the guest VM, task lines do not exist by design; shutdown occurs via channel."""
        from factory.core.cc_backend_executor import task_should_stop
        from factory.core.isolation.guest_bwrap import GUEST_MODE_ENV

        with patch.dict(os.environ, {GUEST_MODE_ENV: "1"}):
            self.assertFalse(task_should_stop("gone-task"))

    def test_isolation_cancel_check_logs_database_stop_source(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO
        from factory.core.worker import _isolation_cancel_check

        self._insert("cancel-source", "cancelling")
        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(_isolation_cancel_check("cancel-source")())
        self.assertIn(
            "isolation.cancel_requested task_id=cancel-source source=task_status_cancelling",
            output.getvalue(),
        )

    def test_isolation_cancel_check_logs_missing_row_source(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO
        from factory.core.worker import _isolation_cancel_check

        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(_isolation_cancel_check("removed-source")())
        self.assertIn(
            "isolation.cancel_requested task_id=removed-source source=task_row_missing",
            output.getvalue(),
        )

    def test_is_cancelling_still_only_matches_cancelling(self) -> None:
        """The old rule remains unchanged: absence of a line is not `cancelling`."""
        from factory.core.cc_backend_executor import task_is_cancelling

        self._insert("work-me", "processing")
        self.assertFalse(task_is_cancelling("work-me"))
        self.assertFalse(task_is_cancelling("gone-task"))
        self._insert("cancel-me", "cancelling")
        self.assertTrue(task_is_cancelling("cancel-me"))


if __name__ == "__main__":
    unittest.main()
