from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from typing import Any

from .runtime import get_db_path
from .schema import initialize_database


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def create_user_input_request(*, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    now = int(time.time())
    db_path = initialize_database(get_db_path())
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO user_input_requests (
                request_id, task_id, status, payload_json, created_at
            ) VALUES (?, ?, 'pending', ?, ?)
            """,
            (request_id, task_id, json.dumps(payload, ensure_ascii=False), now),
        )
        conn.commit()
    return get_user_input_request(request_id) or {}


def get_user_input_request(request_id: str) -> dict[str, Any] | None:
    db_path = initialize_database(get_db_path())
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT request_id, task_id, status, payload_json, response_json,
                   created_at, resolved_at
            FROM user_input_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
    return _row_to_request(row)


def list_pending_user_input_requests(*, task_id: str) -> list[dict[str, Any]]:
    db_path = initialize_database(get_db_path())
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT request_id, task_id, status, payload_json, response_json,
                   created_at, resolved_at
            FROM user_input_requests
            WHERE task_id = ? AND status = 'pending'
            ORDER BY created_at, request_id
            """,
            (task_id,),
        ).fetchall()
    return [item for row in rows if (item := _row_to_request(row)) is not None]


def resolve_user_input_request(
    request_id: str,
    *,
    response: dict[str, Any] | None,
    cancelled: bool = False,
) -> dict[str, Any] | None:
    now = int(time.time())
    status = "cancelled" if cancelled else "submitted"
    response_json = (
        None if response is None else json.dumps(response, ensure_ascii=False)
    )
    db_path = initialize_database(get_db_path())
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute(
            """
            UPDATE user_input_requests
            SET status = ?, response_json = ?, resolved_at = ?
            WHERE request_id = ? AND status = 'pending'
            """,
            (status, response_json, now, request_id),
        )
        conn.commit()
    if cursor.rowcount == 0:
        return None
    return get_user_input_request(request_id)


def wait_for_user_input_response(
    request_id: str,
    *,
    timeout_s: float = 1200,
    interval_s: float = 0.1,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while True:
        request = get_user_input_request(request_id)
        if request is None:
            raise RuntimeError(f"User input request not found: {request_id}")
        status = str(request.get("status") or "")
        if status == "submitted":
            response = request.get("response")
            return response if isinstance(response, dict) else {}
        if status == "cancelled":
            return None
        if time.monotonic() > deadline:
            raise TimeoutError(f"Timeout while waiting for user input request {request_id}")
        time.sleep(interval_s)


def _row_to_request(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = json.loads(row[3]) if row[3] else {}
    response = json.loads(row[4]) if row[4] else None
    return {
        "request_id": row[0],
        "task_id": row[1],
        "status": row[2],
        "payload": payload,
        "response": response,
        "created_at": row[5],
        "resolved_at": row[6],
    }
