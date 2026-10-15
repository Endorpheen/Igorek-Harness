#!/usr/bin/env python3
import sqlite3
import threading
import time
import uuid
from typing import Callable, Optional

from core.logger import get_logger
from core.schema import open_db


class TaskWatchdog:
    def __init__(
        self,
        db_path: str,
        ensure_schema: Callable[[sqlite3.Connection], None],
        interval_s: int = 30,
        stuck_timeout_s: int = 600,
    ):
        self.db_path = db_path
        self.ensure_schema = ensure_schema
        self.interval_s = max(1, int(interval_s))
        self.stuck_timeout_s = max(1, int(stuck_timeout_s))
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.logger = get_logger("watchdog")

    def start(self):
        with self._lock:
            if self.running:
                return
            self.running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            self.logger.info(
                "watchdog_started",
                "Watchdog thread started",
                interval_s=self.interval_s,
                stuck_timeout_s=self.stuck_timeout_s,
            )

    def stop(self):
        with self._lock:
            self.running = False
            if self._thread:
                self._thread.join(timeout=5)
                self._thread = None
            self.logger.info("watchdog_stopped", "Watchdog thread stopped")

    def is_initialized(self) -> bool:
        return self.running

    def _run_loop(self):
        while self.running:
            try:
                self.run_once()
            except Exception as e:
                self.logger.error("watchdog_loop_error", f"Watchdog loop error: {e}")
            for _ in range(self.interval_s):
                if not self.running:
                    break
                time.sleep(1)

    def run_once(self):
        now_ts = int(time.time())
        threshold_ts = now_ts - self.stuck_timeout_s

        with open_db(self.db_path) as conn:
            self.ensure_schema(conn)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Heartbeat (`updated_at`) is the live indicator—worker sends it every 30 seconds while `execute_task` is running.
            # `started_at` is not suitable for this: it is set once at claim time, so
            # a long but fully live task (e.g., a long model round, waiting for a sudo password via phone, heavy Bash script)
            # was incorrectly marked as `queued` during execution. The client displayed 'in queue,' but the worker continued spinning rounds under the same `task_id`,
            # and new profile tasks were not picked up—exactly the incident on 2026-09-23.
            cur.execute(
                """
                SELECT task_id, retry_count, max_retries,
                       COALESCE(updated_at, started_at, created_at, 0) AS activity_ts
                FROM tasks
                WHERE status = 'processing'
                  AND COALESCE(updated_at, started_at, created_at, 0) <= ?
                """,
                (threshold_ts,),
            )
            stuck_rows = cur.fetchall()

            if not stuck_rows:
                return

            for row in stuck_rows:
                task_id = row["task_id"]
                retry_count = int(row["retry_count"] or 0)
                max_retries = int(row["max_retries"] or 0)

                if retry_count < max_retries:
                    new_retry = retry_count + 1
                    cur.execute(
                        """
                        UPDATE tasks
                        SET status = 'queued',
                            retry_count = ?,
                            updated_at = ?,
                            error_message = ?
                        WHERE task_id = ?
                        """,
                        (new_retry, now_ts, "Requeued by watchdog due to stale processing state", task_id),
                    )
                    event_type = "watchdog_requeued"
                    message = f"Task requeued by watchdog ({new_retry}/{max_retries})"
                else:
                    cur.execute(
                        """
                        UPDATE tasks
                        SET status = 'dead_letter',
                            updated_at = ?,
                            completed_at = ?,
                            error_message = ?
                        WHERE task_id = ?
                        """,
                        (now_ts, now_ts, "Moved to dead_letter by watchdog after max retries", task_id),
                    )
                    event_type = "watchdog_dead_letter"
                    message = "Task moved to dead_letter by watchdog"

                event_id = str(uuid.uuid4())
                cur.execute(
                    """
                    INSERT INTO events (event_id, task_id, event_type, profile, details, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        task_id,
                        event_type,
                        "watchdog",
                        message,
                        now_ts,
                    ),
                )
                self.logger.warn(
                    "watchdog_transition",
                    message,
                    task_id=task_id,
                )

            conn.commit()
