from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from .runtime import get_db_path, get_workspace_root
from .schema import initialize_database


def _workspace_files(task_id: str) -> list[str]:
    workspace_root = Path(get_workspace_root())
    workspace_files: list[str] = []

    task_workspace_full = workspace_root / task_id
    task_workspace_temp = workspace_root / f"temp_execution_{task_id[:8]}"

    if task_workspace_full.exists():
        results_dir = task_workspace_full / "results"
        if results_dir.exists():
            for file_path in results_dir.rglob("*"):
                if not file_path.is_file() or file_path.name == "copy_metadata.json":
                    continue
                workspace_files.append(str(file_path.relative_to(results_dir)))

    if task_workspace_temp.exists():
        for file_path in task_workspace_temp.rglob("*"):
            if not file_path.is_file() or file_path.name in {
                "input.json",
                "result.json",
                "copy_metadata.json",
                "cc_backend_trace.jsonl",
            }:
                continue
            workspace_files.append(f"temp/{file_path.relative_to(task_workspace_temp)}")

    return workspace_files


def resolve_workspace_file_paths(task_id: str, workspace_files: list[str]) -> list[str]:
    workspace_root = Path(get_workspace_root())
    resolved: list[str] = []
    task_workspace_full = workspace_root / task_id / "results"
    task_workspace_temp = workspace_root / f"temp_execution_{task_id[:8]}"

    for relative_path in workspace_files:
        if relative_path.startswith("temp/"):
            candidate = task_workspace_temp / relative_path.removeprefix("temp/")
        else:
            candidate = task_workspace_full / relative_path
        if candidate.exists():
            resolved.append(str(candidate.resolve()))

    return resolved


def create_task(
    text: str,
    profile: str = "zai",
    metadata: dict | None = None,
    parent_id: str | None = None,
    input_files: list[str] | None = None,
    _connection: sqlite3.Connection | None = None,
) -> dict:
    db_path = initialize_database(get_db_path()) if _connection is None else None
    task_id = str(uuid.uuid4())
    now = int(time.time())
    normalized_metadata = dict(
        metadata or {"source": "cli", "created_via": "igorek-factory task create"}
    )
    workspace_task_id = (
        str(
            normalized_metadata.get("workspace_task_id") or parent_id or task_id
        ).strip()
        or task_id
    )
    normalized_metadata["workspace_task_id"] = workspace_task_id
    payload = {
        "task_id": task_id,
        "parent_id": parent_id,
        "stage": "main",
        "status": "queued",
        "profile": profile,
        "next_profiles": json.dumps([]),
        "capabilities": json.dumps([]),
        "input_text": text,
        "input_files": json.dumps(list(input_files or [])),
        "limits_max_tokens": 4000,
        "limits_timeout_s": 1200,
        "limits_budget_usd": 0.02,
        "retry_count": 0,
        "max_retries": 3,
        "metadata": json.dumps(normalized_metadata),
        "created_at": now,
        "updated_at": now,
    }

    def insert(conn):
        cursor = conn.cursor()
        columns = list(payload.keys())
        placeholders = ", ".join(["?"] * len(columns))
        cursor.execute(
            f"INSERT INTO tasks ({', '.join(columns)}) VALUES ({placeholders})",
            [payload[column] for column in columns],
        )

    if _connection is not None:
        insert(_connection)
    else:
        with closing(sqlite3.connect(db_path, timeout=10)) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            insert(conn)
            conn.commit()

    return {
        "task_id": task_id,
        "status": "queued",
        "message": "Task created successfully",
    }


def get_task(task_id: str) -> dict | None:
    db_path = initialize_database(get_db_path())
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout = 10000")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT task_id, status, profile, input_text, output_text, metadata,
                   created_at, updated_at, started_at, completed_at, error_message,
                   usage_prompt, usage_completion, usage_lat_ms_p50, usage_lat_ms_p95,
                   approval_blocked_tools
            FROM tasks
            WHERE task_id = ?
            """,
            (task_id,),
        )
        row = cursor.fetchone()

    if not row:
        return None

    metadata: dict | str = {}
    if row[5]:
        try:
            metadata = json.loads(row[5])
        except json.JSONDecodeError:
            metadata = row[5]
    workspace_task_id = metadata.get("workspace_task_id") if isinstance(metadata, dict) else None
    workspace_files_target = workspace_task_id or row[0]

    task = {
        "task_id": row[0],
        "status": row[1],
        "profile": row[2],
        "input_text": row[3],
        "result_text": row[4],
        "output_text": row[4],
        "workspace_files": _workspace_files(workspace_files_target),
        "created_at": row[6],
        "updated_at": row[7],
        "started_at": row[8],
        "completed_at": row[9],
        "usage_prompt": row[11],
        "usage_completion": row[12],
        "usage_lat_ms_p50": row[13],
        "usage_lat_ms_p95": row[14],
        "approval_blocked_tools": [],
    }
    if row[15]:
        try:
            task["approval_blocked_tools"] = json.loads(row[15])
        except json.JSONDecodeError:
            pass
    if metadata:
        task["metadata"] = metadata
    if row[10]:
        task["error_message"] = row[10]
    return task


def list_tasks(limit: int = 20) -> dict:
    db_path = initialize_database(get_db_path())
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout = 10000")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT task_id, status, profile, input_text, output_text, metadata,
                   created_at, updated_at, started_at, completed_at, error_message,
                   usage_prompt, usage_completion, usage_lat_ms_p50, usage_lat_ms_p95
            FROM tasks
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()

    tasks = []
    for row in rows:
        task = {
            "task_id": row[0],
            "status": row[1],
            "profile": row[2],
            "input_text": row[3],
            "result_text": row[4],
            "output_text": row[4],
            "workspace_files": _workspace_files(row[0]),
            "created_at": row[6],
            "updated_at": row[7],
            "started_at": row[8],
            "completed_at": row[9],
            "usage_prompt": row[11],
            "usage_completion": row[12],
            "usage_lat_ms_p50": row[13],
            "usage_lat_ms_p95": row[14],
        }
        if row[5]:
            try:
                task["metadata"] = json.loads(row[5])
            except json.JSONDecodeError:
                task["metadata"] = row[5]
        if row[10]:
            task["error_message"] = row[10]
        tasks.append(task)
    return {"tasks": tasks}


def wait_task(task_id: str, interval_s: float = 2.0, timeout_s: int = 1200) -> dict:
    deadline = time.time() + timeout_s
    final_states = {"ok", "fail", "dead_letter"}

    while True:
        task = get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task not found: {task_id}")
        if task["status"] in final_states:
            return task
        if time.time() > deadline:
            raise TimeoutError(f"Timeout while waiting for task {task_id}")
        time.sleep(interval_s)


def cancel_task(task_id: str) -> bool:
    """Cancel a task.

    A task that is still ``queued`` was never picked up by the worker, so it is
    finalized directly (``fail``) — otherwise it would sit in ``cancelling``
    forever (the worker only selects ``queued`` tasks) and callers waiting for a
    final state would block. A task that is already ``processing`` /
    ``waiting_approval`` is marked ``cancelling`` and the executor stops it
    cooperatively between rounds.
    """
    db_path = initialize_database(get_db_path())
    now = int(time.time())
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout = 10000")
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tasks SET status = 'fail', error_message = 'cancelled by user', "
            "completed_at = ?, updated_at = ? "
            "WHERE task_id = ? AND status = 'queued'",
            (now, now, task_id),
        )
        queued_cancelled = cursor.rowcount > 0
        cursor.execute(
            "UPDATE tasks SET status = 'cancelling', updated_at = ? "
            "WHERE task_id = ? AND status IN ('processing', 'waiting_approval')",
            (now, task_id),
        )
        conn.commit()
        return queued_cancelled or cursor.rowcount > 0


def resume_waiting_approval_task(task_id: str) -> bool:
    """Returns a paused task to the standard worker queue.

    Original prompt and metadata are preserved; callback approval does not execute
    the agent in the Telegram handler and does not share its database connection.
    """
    db_path = initialize_database(get_db_path())
    with closing(sqlite3.connect(db_path, timeout=10)) as conn:
        conn.execute("PRAGMA busy_timeout = 10000")
        cursor = conn.execute(
            """
            UPDATE tasks
            SET status = 'queued', started_at = NULL, completed_at = NULL,
                error_message = NULL, updated_at = ?
            WHERE task_id = ? AND status = 'waiting_approval'
            """,
            (int(time.time()), task_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def ask_task(
    text: str,
    profile: str = "zai",
    interval_s: float = 2.0,
    timeout_s: int = 1200,
    metadata: dict | None = None,
) -> dict:
    created = create_task(text=text, profile=profile, metadata=metadata)
    return wait_task(created["task_id"], interval_s=interval_s, timeout_s=timeout_s)
