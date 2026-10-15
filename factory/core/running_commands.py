"""Live local Bash processes, identified by session and exact tool invocation."""
from __future__ import annotations

import os
import signal
import subprocess
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .runtime import get_db_path
from .schema import open_db
from .vault.logging_redaction import redact_text

_context = ContextVar("running_command_context", default=None)
_SCHEMA = """CREATE TABLE IF NOT EXISTS running_commands (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, task_id TEXT NOT NULL,
    tool_use_id TEXT NOT NULL, command TEXT NOT NULL, started_at REAL NOT NULL,
    owner_pid INTEGER NOT NULL, owner_start TEXT NOT NULL,
    pid INTEGER NOT NULL, pid_start TEXT NOT NULL, stop_requested INTEGER NOT NULL DEFAULT 0
)"""


def _start(pid: int) -> str:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        return fields[19] if fields[0] != "Z" else ""
    except (OSError, IndexError):
        return ""


@contextmanager
def command_context(session_id: str | None, task_id: str | None, tool_use_id: str | None):
    token = _context.set((str(session_id), str(task_id), str(tool_use_id)) if session_id and task_id and tool_use_id else None)
    try:
        yield
    finally:
        _context.reset(token)


def _live(row) -> bool:
    return bool(row[7] and row[9] and _start(row[6]) == row[7] and _start(row[8]) == row[9])


def list_commands(session_id: str) -> list[dict]:
    with open_db(get_db_path()) as conn:
        conn.execute(_SCHEMA)
        rows = conn.execute("SELECT * FROM running_commands WHERE session_id = ? ORDER BY started_at", (session_id,)).fetchall()
        result = []
        for row in rows:
            if _live(row):
                result.append({"id": row[0], "kind": "command", "task_id": row[2], "tool_use_id": row[3],
                               "label": row[4], "started_at": row[5], "stopping": bool(row[10])})
            else:
                conn.execute("DELETE FROM running_commands WHERE id = ?", (row[0],))
        return result


def request_stop(session_id: str, identity: str) -> bool:
    if not any(item["id"] == identity for item in list_commands(session_id)):
        return False
    with open_db(get_db_path()) as conn:
        return conn.execute("UPDATE running_commands SET stop_requested = 1 WHERE id = ? AND session_id = ?",
                            (identity, session_id)).rowcount == 1


def _terminate(process, *, force=False):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass


def run_process(argv, *, label: str, guest_privileged=False, **kwargs):
    from .isolation.guest_bwrap import guest_mode_requested
    if guest_mode_requested() and not guest_privileged:
        from .isolation.guest_process import unprivileged_argv
        argv = unprivileged_argv(argv, shell=kwargs.pop('shell', False),
                                 executable=kwargs.pop('executable', None))
    context = _context.get()
    if not context or not Path("/proc/self/stat").is_file():
        return subprocess.run(argv, **kwargs)
    if guest_mode_requested():
        return subprocess.run(argv, **kwargs)
    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)
    capture = kwargs.pop("capture_output", False)
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    identity = "cmd-" + uuid.uuid4().hex
    db = get_db_path()
    with open_db(db) as conn:
        conn.execute(_SCHEMA)
    process = subprocess.Popen(argv, start_new_session=True, **kwargs)
    registered = False
    try:
        with open_db(db) as conn:
            conn.execute("INSERT INTO running_commands VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                         (identity, *context, redact_text(label)[:1000], time.time(),
                          os.getpid(), _start(os.getpid()), process.pid, _start(process.pid)))
        registered = True
        deadline = time.monotonic() + timeout if timeout is not None else None
        stopped = False
        while True:
            try:
                output, error = process.communicate(timeout=min(.25, max(.001, deadline - time.monotonic())) if deadline else .25)
                break
            except subprocess.TimeoutExpired:
                with open_db(db) as conn:
                    row = conn.execute("SELECT stop_requested FROM running_commands WHERE id = ?", (identity,)).fetchone()
                if row and row[0]:
                    _terminate(process)
                    try:
                        output, error = process.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        _terminate(process, force=True)
                        output, error = process.communicate()
                    stopped = True
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    _terminate(process, force=True)
                    output, error = process.communicate()
                    raise subprocess.TimeoutExpired(argv, timeout, output=output, stderr=error)
        if stopped:
            error = (error or "") + "\nCommand stopped by user"
        result = subprocess.CompletedProcess(argv, process.returncode, output, error)
        if check:
            result.check_returncode()
        return result
    finally:
        if process.poll() is None:
            _terminate(process, force=True)
            process.communicate()
        if registered:
            with open_db(db) as conn:
                conn.execute("DELETE FROM running_commands WHERE id = ?", (identity,))
