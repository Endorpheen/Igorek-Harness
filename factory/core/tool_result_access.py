"""Durable, read-only access to results issued by the runtime."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .artifact_files import open_regular_file
from .schema import open_db


_CREATE = """CREATE TABLE IF NOT EXISTS tool_result_archives (
    path TEXT PRIMARY KEY, task_id TEXT NOT NULL, session_id TEXT NOT NULL,
    workdir TEXT NOT NULL, device TEXT NOT NULL, inode TEXT NOT NULL,
    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, created_at INTEGER NOT NULL
)"""


def _identity(handle):
    stat = os.fstat(handle.fileno())
    return str(stat.st_dev), str(stat.st_ino), stat.st_size, stat.st_mtime_ns


@dataclass(frozen=True)
class ToolResultAccess:
    workdir: str
    artifact_workdir: str
    task_id: str
    session_id: str
    database: str

    @classmethod
    def create(cls, workdir, artifact_workdir, task_id=None, session_id=None, database=None):
        if database is None:
            from .runtime import get_db_path
            database = get_db_path()
        return cls(os.path.normcase(os.path.realpath(workdir)), os.path.normcase(os.path.realpath(artifact_workdir)),
                   task_id or uuid4().hex, session_id or "", str(Path(database).absolute()))

    def register(self, path):
        path = Path(os.path.normcase(os.path.abspath(path)))
        if path.parent != Path(self.artifact_workdir) / ".tool_results" or path.suffix != ".txt":
            raise ValueError("Result is outside the issuing runtime's output directory")
        with open_regular_file(path) as handle:
            identity = _identity(handle)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with open_db(self.database) as conn:
            conn.execute(_CREATE)
            previous = conn.execute("SELECT task_id, session_id, workdir, device, inode, size, mtime_ns FROM tool_result_archives WHERE path=?", (str(path),)).fetchone()
            if previous and previous[:3] != (self.task_id, self.session_id, self.workdir):
                raise ValueError("Result path was issued by another task")
            if previous and previous[3:] != identity:
                raise ValueError("Result identity changed after it was issued")
            conn.execute("INSERT OR IGNORE INTO tool_result_archives VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (str(path), self.task_id, self.session_id, self.workdir, *identity, int(time.time())))

    def _legacy_owner(self, path):
        parent = Path(path).parent
        if parent.name != ".tool_results" or Path(path).suffix != ".txt":
            return None
        owners = [(self.task_id, Path(self.artifact_workdir))]
        if self.session_id and parent != Path(self.artifact_workdir) / ".tool_results":
            from .runtime import get_workspace_root
            with closing(sqlite3.connect(Path(self.database).absolute().as_uri() + "?mode=ro", uri=True)) as conn:
                try:
                    rows = conn.execute("SELECT task_id, metadata FROM tasks WHERE json_extract(CASE WHEN json_valid(metadata) THEN metadata ELSE '{}' END, '$.session')=?", (self.session_id,)).fetchall()
                except sqlite3.OperationalError as exc:
                    if "no such table" not in str(exc):
                        raise
                    rows = []
            for task, metadata in rows:
                cwd = json.loads(metadata).get("cwd")
                if isinstance(cwd, str) and os.path.normcase(os.path.realpath(cwd)) == self.workdir:
                    owners.append((task, Path(get_workspace_root()) / ("temp_execution_" + task[:8])))
        for task, root in owners:
            if parent != root / ".tool_results" or Path(path).suffix != ".txt":
                continue
            try:
                with open_regular_file(root / "cc_backend_trace.jsonl") as handle:
                    remaining = 16 * 1024 * 1024
                    while remaining > 0:
                        line = handle.readline(min(remaining, 1024 * 1024))
                        if not line:
                            break
                        remaining -= len(line)
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if not isinstance(event, dict) or event.get("kind") != "tool.result":
                            continue
                        raw, visible = event.get("raw_result_len"), event.get("result_for_model_len")
                        if type(raw) is not int or type(visible) is not int or raw <= visible:
                            continue
                        output = event.get("context_output")
                        if not isinstance(output, str) or not output.startswith("<persisted-output>\n"):
                            continue
                        pointer = next((line.removeprefix("Full output saved to: ") for line in output.splitlines() if line.startswith("Full output saved to: ")), "")
                        if pointer and os.path.normcase(os.path.abspath(pointer)) == path:
                            return ToolResultAccess(self.workdir, str(root), task, self.session_id, self.database)
            except FileNotFoundError:
                continue
        return None

    def open_issued(self, path, *, recover_legacy=True):
        """Return a pinned regular file only when this task/session was issued it."""
        path = os.path.normcase(os.path.abspath(path))
        if not Path(self.database).is_file():
            return None
        with closing(sqlite3.connect(Path(self.database).absolute().as_uri() + "?mode=ro", uri=True)) as conn:
            try:
                row = conn.execute("SELECT task_id, session_id, workdir, device, inode, size, mtime_ns FROM tool_result_archives WHERE path=?", (path,)).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
                row = None
        if row is None and recover_legacy:
            owner = self._legacy_owner(path)
            if owner is not None:
                owner.register(path)
                return self.open_issued(path, recover_legacy=False)
        if row is None or row[2] != self.workdir:
            return None
        if not ((row[0] == self.task_id and row[1] == self.session_id) or
                (self.session_id and row[1] == self.session_id)):
            return None
        try:
            handle = open_regular_file(path)
        except FileNotFoundError as exc:
            raise FileNotFoundError("The saved tool result was removed; its full text is unavailable. Do not repeat the original action without checking its outcome.") from exc
        if _identity(handle) != row[3:]:
            handle.close()
            raise ValueError("The saved tool result changed after it was issued; inspect the current state before retrying")
        return handle


def has_retained_results(directory, database):
    """Keep new and legacy archives while their task remains in durable history."""
    if not Path(database).is_file():
        return False
    prefix = os.path.normcase(str(Path(directory).absolute())) + os.sep
    with closing(sqlite3.connect(Path(database).absolute().as_uri() + "?mode=ro", uri=True)) as conn:
        try:
            rows = conn.execute("""SELECT path FROM tool_result_archives a WHERE
                EXISTS (SELECT 1 FROM tasks t WHERE t.task_id=a.task_id) OR
                (a.session_id<>'' AND EXISTS (SELECT 1 FROM tasks t WHERE json_extract(CASE WHEN json_valid(t.metadata) THEN t.metadata ELSE '{}' END,'$.session')=a.session_id))""").fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            rows = []
        if any(path.startswith(prefix) for (path,) in rows):
            return True
        name = Path(directory).name
        if not name.startswith("temp_execution_") or not (Path(directory) / ".tool_results").is_dir():
            return False
        task_prefix = name.removeprefix("temp_execution_")
        if len(task_prefix) != 8 or any(char not in "0123456789abcdef" for char in task_prefix):
            return False
        try:
            return conn.execute("SELECT 1 FROM tasks WHERE substr(task_id,1,8)=? LIMIT 1", (task_prefix,)).fetchone() is not None
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return False
