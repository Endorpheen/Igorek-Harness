"""Persistent steer queue for session messages.

This mirrors desktop chat (``factory.core.message_queue.MessageQueue``) across
process boundaries such as the mobile bridge and Telegram. While a session task
is active, new messages are stored against that same task ID in the database.

The CC backend consumes queued messages during the active turn through
``pending_steer_provider`` and continues under the same task ID.

Desktop chat keeps its in-memory ``MessageQueue`` as the source of truth. This
module is only for queues that must survive process boundaries and restarts.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from typing import Any

from .runtime import get_db_path
from .schema import initialize_database, open_db


def _json_list(value: Any) -> str:
    if not isinstance(value, (list, tuple)):
        return "[]"
    return json.dumps([str(item) for item in value], ensure_ascii=False)


def _parse_list(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def enqueue_steer(
    *,
    session_id: str,
    task_id: str,
    text: str,
    message_id: str = "",
    input_files: list[str] | None = None,
    selected_skills: list[str] | None = None,
) -> int:
    """Queue a user message for an already running task.

    Returns the queue row ID.
    """
    identifier = str(task_id or "").strip()
    if not identifier:
        raise ValueError("task_id is required for steer queue")
    db_path = initialize_database(get_db_path())
    with open_db(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO session_steer_messages "
            "(session_id, task_id, message_id, text, input_files_json, selected_skills_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(session_id or ""),
                identifier,
                str(message_id or ""),
                str(text or ""),
                _json_list(input_files),
                _json_list(selected_skills),
                int(time.time()),
            ),
        )
        return int(cursor.lastrowid or 0)


def drain_steer(task_id: str, *, prioritized_only: bool = False) -> list[dict[str, Any]]:
    """Atomically drain and mark a task's queued messages as delivered.

    Returns ``{"text", "message_id", "input_files", "selected_skills"}`` items
    in insertion order. A concurrent second drain returns an empty list.
    """
    identifier = str(task_id or "").strip()
    if not identifier:
        return []
    db_path = initialize_database(get_db_path())
    priority_clause = " AND id < 0" if prioritized_only else ""
    now = int(time.time())
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT id, message_id, text, input_files_json, selected_skills_json "
                "FROM session_steer_messages "
                f"WHERE task_id = ? AND consumed_at IS NULL{priority_clause} ORDER BY id",
                (identifier,),
            ).fetchall()
            if not rows:
                conn.rollback()
                return []
            conn.execute(
                "UPDATE session_steer_messages SET consumed_at = ? "
                f"WHERE task_id = ? AND consumed_at IS NULL{priority_clause}",
                (now, identifier),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return [
        {
            "text": str(row[2] or ""),
            "message_id": str(row[1] or ""),
            "input_files": _parse_list(row[3]),
            "selected_skills": _parse_list(row[4]),
        }
        for row in rows
    ]


def pending_steer_count(task_id: str) -> int:
    """Return the number of messages not yet delivered to a task."""
    identifier = str(task_id or "").strip()
    if not identifier:
        return 0
    db_path = initialize_database(get_db_path())
    with open_db(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM session_steer_messages "
            "WHERE task_id = ? AND consumed_at IS NULL",
            (identifier,),
        ).fetchone()
    return int(row[0]) if row else 0


def prioritize_steer(task_id: str, message_id: str, session_id: str) -> bool:
    identifier = str(task_id or "").strip()
    message = str(message_id or "").strip()
    session = str(session_id or "").strip()
    if not identifier or not message or not session:
        return False
    db_path = initialize_database(get_db_path())
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT id FROM session_steer_messages "
                "WHERE task_id = ? AND session_id = ? AND message_id = ? "
                "AND consumed_at IS NULL ORDER BY id LIMIT 1",
                (identifier, session, message),
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            # Negative IDs (priority) remain in the table even after delivery, so the next ID is taken below the GLOBAL minimum
            # across the entire table, not just for pending rows. Otherwise, UPDATE would conflict with the UNIQUE(id) constraint
            # for already delivered messages.
            minimum = conn.execute(
                "SELECT MIN(id) FROM session_steer_messages"
            ).fetchone()
            next_id = min(int(minimum[0] or 0) - 1, -1)
            conn.execute(
                "UPDATE session_steer_messages SET id = ? WHERE id = ?",
                (next_id, int(row[0])),
            )
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise


def cancel_steer(task_id: str, message_id: str, session_id: str) -> bool:
    """Remove a queued (not yet delivered) message from the steer queue.

    Returns ``True`` if the message was still waiting and got removed.
    A message that the running task already consumed cannot be cancelled:
    there is no product-level support for retracting a delivered turn.
    """
    identifier = str(task_id or "").strip()
    message = str(message_id or "").strip()
    session = str(session_id or "").strip()
    if not identifier or not message or not session:
        return False
    db_path = initialize_database(get_db_path())
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT id FROM session_steer_messages "
                "WHERE task_id = ? AND session_id = ? AND message_id = ? "
                "AND consumed_at IS NULL ORDER BY id LIMIT 1",
                (identifier, session, message),
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            conn.execute("DELETE FROM session_steer_messages WHERE id = ?", (int(row[0]),))
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise


def pending_steer_sessions() -> dict[str, list[dict[str, Any]]]:
    """Return pending messages grouped by task ID."""
    db_path = initialize_database(get_db_path())
    with open_db(db_path) as conn:
        rows = conn.execute(
            "SELECT id, session_id, task_id, message_id, text, input_files_json "
            "FROM session_steer_messages WHERE consumed_at IS NULL ORDER BY id",
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[2]), []).append(
            {
                "id": int(row[0]),
                "session_id": str(row[1] or ""),
                "task_id": str(row[2] or ""),
                "message_id": str(row[3] or ""),
                "text": str(row[4] or ""),
                "input_files": _parse_list(row[5]),
            }
        )
    return grouped


def make_steer_provider(task_id: str) -> Callable[[], list[dict[str, Any]]]:
    """Build a CC-backend provider that drains this task's queue."""

    def _provider() -> list[dict[str, Any]]:
        try:
            return drain_steer(task_id)
        except Exception:
            # Queue failures must not break the task's active turn.
            return []

    return _provider


def make_priority_steer_provider(task_id: str) -> Callable[[], list[dict[str, Any]]]:
    """Drain only messages explicitly sent ahead of the normal queue."""

    def _provider() -> list[dict[str, Any]]:
        try:
            return drain_steer(task_id, prioritized_only=True)
        except Exception:
            return []

    return _provider
