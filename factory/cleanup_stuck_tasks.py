#!/usr/bin/env python3
"""Manual cleanup of stuck tasks in Agent Factory.

Marks tasks in the `processing` status that have been inactive for a long time with the final status `dead_letter`. Records are **not deleted**: the mobile log (`factory/core/mobile_bridge/events.py`) publishes `task.status` based on the final status, allowing the client to correctly unlock. Previously, both the CLI and worker contained `DELETE FROM tasks`, which caused a task record to disappear from a still-running worker: client task cards vanished, and there was no way to cancel the task.

**Important:** In the runtime environment, this task is handled by the worker itself (`Worker.cleanup_stuck_tasks`) and only according to its own profile. Live tasks are protected by a heartbeat that updates `tasks.updated_at` during `execute_task`. This CLI is only used for manual incident troubleshooting.
"""

import sqlite3
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

FACTORY_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(FACTORY_DIR))

from core.runtime import get_db_path  # noqa: E402
from core.schema import open_db  # noqa: E402

# The canonical database path is taken from runtime
# ($HOME/.igorek-agent-factory/database/factory.db). The previous constant
# FACTORY_DIR/database/factory.db pointed to a repository directory where the working
# database does not exist, so the CLI silently failed to locate it.
DB_PATH = get_db_path()


def cleanup_stuck_tasks(timeout_seconds=300, profile=None):
    """Marks stuck `processing` tasks with the `dead_letter` status.

    If a `profile` is specified, only tasks of this profile are processed—exactly as the worker does in runtime.
    """
    try:
        with open_db(DB_PATH) as conn:
            cursor = conn.cursor()

            # Find stuck tasks
            query = """
                SELECT task_id, profile, input_text, created_at
                FROM tasks
                WHERE status = 'processing'
                  AND updated_at < strftime('%s', 'now') - ?
            """
            params = [timeout_seconds]
            if profile:
                query += " AND profile = ?"
                params.append(profile)

            cursor.execute(query, tuple(params))

            stuck_tasks = cursor.fetchall()

            if not stuck_tasks:
                print(f"✅ Зависших задач не найдено (timeout: {timeout_seconds}s)")
                return

            print(f"⚠️  Найдено {len(stuck_tasks)} зависших задач:")

            for task_id, task_profile, input_text, created_at in stuck_tasks:
                # Show task details
                text_preview = input_text[:100] + "..." if len(input_text) > 100 else input_text
                print(f"  - {task_id[:8]}... [{task_profile}] {text_preview}")

                # Mark as dead_letter, save record
                cursor.execute("""
                    UPDATE tasks
                    SET status = 'dead_letter',
                        error_message = ?,
                        completed_at = strftime('%s', 'now'),
                        updated_at = strftime('%s', 'now')
                    WHERE task_id = ?
                      AND status = 'processing'
                """, (
                    "cleanup_stuck_tasks: no heartbeat for "
                    f"{timeout_seconds}s (profile {task_profile})",
                    task_id,
                ))

            conn.commit()
            print(f"\n✅ {len(stuck_tasks)} зависших задач помечено dead_letter")

    except Exception as e:
        print(f"❌ Ошибка очистки: {e}")
        sys.exit(1)


def main():
    timeout = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    profile = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"🧹 Запуск очистки зависших задач...")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"⏱️  Таймаут: {timeout} секунд ({timeout//60} минут)")
    if profile:
        print(f"🎯 Профиль: {profile}")
    print()

    cleanup_stuck_tasks(timeout, profile)


if __name__ == '__main__':
    main()
