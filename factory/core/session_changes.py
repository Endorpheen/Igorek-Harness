"""Per-task changed-file stats and per-session aggregation.

The cc-backend executor records, for every successful Write/Edit, how many
lines a task added or removed from each file. The primary source is the
checkpoint snapshot taken before the first write of the task; when no
checkpoint is available the stats are derived from the tool input itself.

Records are persisted as one JSON file per task under
``<data_root>/session_changes/<task_id>.json`` — readable later by the mobile
bridge without a database migration. ``aggregate_changes`` folds a session's
records into the frozen mobile contract.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .change_file_types import non_text_change_kind
from .durable_root_scope import durable_root_within_workdir, is_within_relative
from .runtime import get_data_root

CHANGES_DIRNAME = "session_changes"
INTERNAL_DIRS = frozenset({".checkpoints", ".tool_results", ".codex"})
VALID_STATUSES = frozenset({"added", "modified", "deleted"})
_SAFE_TASK_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def _data_root(data_root: str | None) -> Path:
    return Path(data_root) if data_root else get_data_root()


def _task_changes_path(task_id: str, *, data_root: str | None = None) -> Path:
    safe = _SAFE_TASK_ID.sub("_", str(task_id or "")).strip("._") or "task"
    return _data_root(data_root) / CHANGES_DIRNAME / f"{safe}.json"


def _read_text(path: str | os.PathLike[str]) -> str:
    from .artifact_files import read_regular_text
    try:
        return read_regular_text(Path(path).absolute(), errors="replace")
    except FileNotFoundError:
        return ""


def count_line_changes(old_text: str, new_text: str) -> tuple[int, int]:
    """Return (additions, deletions) between two texts using the TUI diff logic."""
    from .tui.diff_render import DiffRenderer

    diff = DiffRenderer().render_unified_diff(
        old_text or "",
        new_text or "",
        context_lines=0,
        max_lines=1_000_000_000,
    )
    additions = sum(1 for line in diff[2:] if line.startswith("+"))
    deletions = sum(1 for line in diff[2:] if line.startswith("-"))
    return additions, deletions


def _count_lines(text: str) -> int:
    return len((text or "").splitlines())


def _relative_to_workdir(abs_path: str, workdir: str) -> str | None:
    try:
        return Path(abs_path).resolve().relative_to(Path(workdir).resolve()).as_posix()
    except (ValueError, OSError):
        return None


def is_internal_path(relative_path: str, *, durable_root: str | None = None) -> bool:
    """True when a path lives under a runtime-artifact directory.

    ``durable_root`` (workdir-relative) is passed in isolation/kvm mode, where
    the configured durable root carries the runtime artifacts; off mode passes
    nothing, so a directory that merely shares the name is user data.
    """
    parts = Path(str(relative_path or "")).parts
    if any(part in INTERNAL_DIRS for part in parts):
        return True
    return bool(durable_root) and is_within_relative(relative_path, durable_root)


def write_task_changes(record: dict[str, Any], *, data_root: str | None = None) -> None:
    path = _task_changes_path(str(record.get("task_id") or ""), data_root=data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def load_task_changes(task_id: str, *, data_root: str | None = None) -> dict[str, Any] | None:
    path = _task_changes_path(task_id, data_root=data_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


class TaskChangeTracker:
    """Accumulates per-file line stats for one cc-backend task and persists them."""

    def __init__(
        self,
        *,
        task_id: str,
        workdir: str,
        checkpoint_manager: Any = None,
        data_root: str | None = None,
    ) -> None:
        self.task_id = str(task_id or "")
        self.workdir = str(workdir)
        self.checkpoint_manager = checkpoint_manager
        self.data_root = str(data_root) if data_root else str(get_data_root())
        self._durable_root = durable_root_within_workdir(
            workdir=self.workdir, data_root=self.data_root
        )
        self._files: dict[str, dict[str, Any]] = {}
        self._baselines: dict[str, tuple[bool, str | None]] = {}
        self._baseline_revision = ""
        self._shell_partial = False

    # -- baseline lookup -------------------------------------------------

    def _baseline(self, abs_path: str) -> tuple[bool, str | None]:
        cached = self._baselines.get(abs_path)
        if cached is not None:
            return cached
        if self.checkpoint_manager is not None:
            try:
                available, content = self.checkpoint_manager.baseline_content(
                    target_path=abs_path,
                    workdir=self.workdir,
                )
            except Exception:
                available, content = False, None
            if available:
                if not self._baseline_revision:
                    revision_reader = getattr(self.checkpoint_manager, "baseline_revision", None)
                    if callable(revision_reader):
                        revision = revision_reader(target_path=abs_path, workdir=self.workdir)
                        if isinstance(revision, str):
                            self._baseline_revision = revision
                self._baselines[abs_path] = (True, content)
                return True, content
        self._baselines[abs_path] = (False, None)
        return False, None

    # -- recording -------------------------------------------------------

    def record_write(self, path: str, content: str) -> None:
        self._record(path, tool_name="Write", old_string="", new_string="", content=content or "")

    def record_edit(self, path: str, old_string: str, new_string: str) -> None:
        self._record(path, tool_name="Edit", old_string=old_string or "", new_string=new_string or "", content="")

    def workspace_snapshot(self) -> dict[str, tuple] | None:
        """Capture bounded file metadata around a shell command, without file contents."""
        root = Path(self.workdir).resolve()
        result = {}
        errors = []
        directories = 0
        for directory, folders, files in os.walk(root, followlinks=False, onerror=errors.append):
            directories += 1
            if directories > 10000:
                return None
            folders[:] = [name for name in folders if not name.startswith('.')
                          and name not in {'node_modules', '__pycache__'}
                          and not (Path(directory) / name).is_symlink()]
            for name in files:
                path = Path(directory) / name
                if name.startswith('.') or path.is_symlink():
                    continue
                try:
                    info = path.stat()
                    if not path.is_file():
                        continue
                except OSError:
                    continue
                result[path.relative_to(root).as_posix()] = (info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino)
                if len(result) >= 10000:
                    return None
        return None if errors else result

    def record_shell_changes(self, before: dict[str, tuple] | None) -> None:
        """Persist observed workspace changes even when shell output names no files."""
        after = self.workspace_snapshot()
        if before is None or after is None:
            self._shell_partial = True
            self._persist()
            return
        for relative in before.keys() | after.keys():
            if before.get(relative) == after.get(relative) or is_internal_path(relative, durable_root=self._durable_root):
                continue
            path = str(Path(self.workdir) / relative)
            available, baseline = self._baseline(path)
            if available:
                self._record_from_checkpoint(relative, path, baseline)
            else:
                status = 'added' if relative not in before else 'deleted' if relative not in after else 'modified'
                if relative in self._files and status == 'modified':
                    continue
                self._store(relative, 0, 0, status, force=True)
        self._persist()

    def _record(
        self,
        path: str,
        *,
        tool_name: str,
        old_string: str,
        new_string: str,
        content: str,
    ) -> None:
        relative = _relative_to_workdir(path, self.workdir)
        if relative is None or is_internal_path(relative, durable_root=self._durable_root):
            return

        available, baseline = self._baseline(path)
        if available:
            self._record_from_checkpoint(relative, path, baseline)
        else:
            self._record_from_tool_input(relative, tool_name, old_string, new_string, content)
        self._persist()

    def _record_from_checkpoint(self, relative: str, path: str, baseline: str | None) -> None:
        exists = os.path.isfile(path)
        if not exists and baseline is None:
            return
        if non_text_change_kind(relative):
            status = "added" if baseline is None else "deleted" if not exists else "modified"
            self._store(relative, 0, 0, status, force=True)
            return
        current = _read_text(path) if exists else ""
        if baseline is None:
            additions, deletions = count_line_changes("", current)
            status = "added"
        elif not exists:
            additions, deletions = count_line_changes(baseline, "")
            status = "deleted"
        else:
            additions, deletions = count_line_changes(baseline, current)
            status = "modified"
        self._store(relative, additions, deletions, status)

    def _record_from_tool_input(
        self,
        relative: str,
        tool_name: str,
        old_string: str,
        new_string: str,
        content: str,
    ) -> None:
        previous = self._files.get(relative)
        if non_text_change_kind(relative):
            status = "added" if previous is None and tool_name == "Write" else "modified"
            self._store(relative, 0, 0, status, force=True)
            return
        if tool_name == "Write":
            operation_additions = _count_lines(content)
            operation_deletions = 0
        else:
            operation_additions, operation_deletions = count_line_changes(old_string, new_string)
        if previous is None:
            additions, deletions = operation_additions, operation_deletions
            status = "added" if tool_name == "Write" else "modified"
        else:
            additions = int(previous["additions"]) + operation_additions
            deletions = int(previous["deletions"]) + operation_deletions
            status = "modified"
        self._store(relative, additions, deletions, status)

    def _store(self, relative: str, additions: int, deletions: int, status: str, *, force: bool = False) -> None:
        if non_text_change_kind(relative):
            additions, deletions, force = 0, 0, True
        if not force and status == "modified" and additions == 0 and deletions == 0:
            self._files.pop(relative, None)
            return
        self._files[relative] = {
            "path": relative,
            "additions": int(additions),
            "deletions": int(deletions),
            "status": status,
        }

    def _persist(self) -> None:
        if not self.task_id:
            return
        record = {
            "task_id": self.task_id,
            "workdir": self.workdir,
            "updated_at": int(time.time()),
            "baseline_revision": self._baseline_revision,
            "files": [self._files[key] for key in sorted(self._files)],
            **({"tracking_partial": True} if self._shell_partial else {}),
        }
        write_task_changes(record, data_root=self.data_root)

    def files(self) -> list[dict[str, Any]]:
        return [self._files[key] for key in sorted(self._files)]


def _normalize_path(relative: str, task_workdir: str, session_workdir: str) -> str | None:
    path = Path(str(relative or ""))
    if not path.parts:
        return None
    if path.is_absolute():
        return _relative_to_workdir(str(path), session_workdir)
    resolved = Path(task_workdir) / path
    # security: a relative path resolving outside the workdir (e.g. ``../x``) is dropped, never leaked
    return _relative_to_workdir(str(resolved), session_workdir)


def normalize_workdir_path(path: str, *, workdir: str) -> str | None:
    """Resolve a client-supplied path to a workdir-relative posix path.

    Returns ``None`` when the path is empty or resolves outside ``workdir``.
    """
    return _normalize_path(str(path or ""), workdir, workdir)


def aggregate_changes(
    records: list[dict[str, Any]],
    *,
    session_workdir: str,
    durable_root: str | None = None,
) -> dict[str, Any]:
    """Fold per-task records (oldest first) into the mobile ``changes`` contract.

    ``durable_root`` (workdir-relative, kvm mode only) repeats the tracker's
    exclusion at aggregation time, so a durable-root path already present in a
    persisted record never reaches the mobile client. Off mode passes nothing.
    """
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        task_workdir = str(record.get("workdir") or session_workdir)
        raw_files = record.get("files")
        if not isinstance(raw_files, list):
            continue
        for entry in raw_files:
            if not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "")
            if status not in VALID_STATUSES:
                continue
            relative = _normalize_path(str(entry.get("path") or ""), task_workdir, session_workdir)
            if not relative or is_internal_path(relative, durable_root=durable_root):
                continue
            if non_text_change_kind(relative):
                additions, deletions = 0, 0
            else:
                additions = int(entry.get("additions") or 0)
                deletions = int(entry.get("deletions") or 0)
            existing = merged.get(relative)
            if existing is None:
                merged[relative] = {
                    "path": relative,
                    "additions": additions,
                    "deletions": deletions,
                    "status": status,
                }
            else:
                existing["additions"] += additions
                existing["deletions"] += deletions
                existing["status"] = status

    files = [merged[key] for key in sorted(merged)]
    return {
        "files": files,
        **({"tracking_partial": True} if any(record.get("tracking_partial") for record in records if isinstance(record, dict)) else {}),
        "totals": {
            "files": len(files),
            "additions": sum(item["additions"] for item in files),
            "deletions": sum(item["deletions"] for item in files),
        },
    }
