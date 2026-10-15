"""Observability for ``delegate_task`` subagents.

A child run executes synchronously inside its parent's tool call without a
separate ``tasks`` row, so the bridge/Web UI cannot observe it as a normal task.
Give each child a stable identifier and durable lifecycle journal.

Why a file rather than a database table
--------------------------------------
The parent can run inside a session VM (``IGOREK_ISOLATION_MODE=kvm``). The host
cannot see the guest database, but virtiofs exports the task workspace. A file
in the task's ``artifact_workdir`` (``subagents.jsonl``) is therefore shared by
host and guest. The host bridge imports rows into ``mobile_events`` for SSE/API.

Use JSONL so appends survive process failure; readers ignore an incomplete final
line instead of treating it as a complete event.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from .artifact_files import atomic_write_file, open_regular_file

SUBAGENTS_JOURNAL_NAME = "subagents.jsonl"
SUBAGENTS_CANCEL_DIR_NAME = "subagents_cancel"

# Subagent event log entries (raw file). Acts as a bridge mapping them into kind types
# ``subagent.*`` of the mobile log; these are different layers, similar to cc-backend trace.
EVENT_START = "start"
EVENT_PROGRESS = "progress"
EVENT_FINISH = "finish"

# Subagent operational statuses.
STATUS_RUNNING = "running"
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = (STATUS_RUNNING, "waiting_approval", "waiting_input")
FINAL_STATUSES = (STATUS_OK, STATUS_ERROR, STATUS_CANCELLED)

_MAX_TITLE_CHARS = 200
_MAX_SUMMARY_CHARS = 4_000
_MAX_NOTE_CHARS = 200

_WHITESPACE_RE = re.compile(r"\s+")
_SUBAGENT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def subagents_journal_path(artifact_workdir: str | os.PathLike[str] | None) -> Path | None:
    """Path to the subagent task log (``None`` if no working directory)."""
    if not artifact_workdir:
        return None
    return Path(artifact_workdir) / SUBAGENTS_JOURNAL_NAME


def subagents_cancel_dir(artifact_workdir: str | os.PathLike[str] | None) -> Path | None:
    """Directory of flag files for subagent address cancellation."""
    if not artifact_workdir:
        return None
    return Path(artifact_workdir).expanduser().resolve() / SUBAGENTS_CANCEL_DIR_NAME


def new_subagent_id() -> str:
    """A stable child process identifier."""
    return "sub_" + uuid.uuid4().hex[:12]


def safe_goal_title(goal: str, *, limit: int = _MAX_TITLE_CHARS) -> str:
    """A secure task header: the first non-empty line without line breaks.

    The model may place a service context or lengthy code in ``goal`` — only a short, human-readable string is sent to the web.
    """
    text = str(goal or "")
    for line in text.splitlines():
        cleaned = _WHITESPACE_RE.sub(" ", line).strip()
        if cleaned:
            return cleaned[:limit]
    return ""


def clip_summary(text: str, *, limit: int = _MAX_SUMMARY_CHARS) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "…"


class SubagentReporter:
    """Writes the child process lifecycle into the task log.

    One reporter per task; ``record()`` is called by the parent (delegate_task)
    and from the child progress callback. Recording errors are suppressed: observation
    should not disrupt task execution.
    """

    def __init__(
        self,
        *,
        artifact_workdir: str | os.PathLike[str] | None,
        session_id: str | None = None,
        parent_task_id: str = "",
    ) -> None:
        self.artifact_workdir = artifact_workdir
        self.session_id = str(session_id or "") or None
        self.parent_task_id = str(parent_task_id or "")

    @property
    def enabled(self) -> bool:
        return bool(self.artifact_workdir)

    def record(self, event: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        path = subagents_journal_path(self.artifact_workdir)
        if path is None:
            return
        from .cc_backend_trace import agent_trace_context, agent_session_id
        context = agent_trace_context()
        subagent = str(payload.get("subagent_id") or "")
        frame = {
            "agent_session_id": agent_session_id(subagent),
            "parent_session_id": context.get("agent_session_id") or self.session_id or "",
            "root_session_id": context.get("root_session_id") or self.session_id or "",
            "ts": int(time.time()),
            "event": str(event),
            "session_id": self.session_id or "",
            "parent_task_id": self.parent_task_id or "",
            **{str(key): value for key, value in payload.items()},
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(frame, ensure_ascii=False) + "\n")
        except OSError:
            # Observations should not disrupt execution: the log is a secondary layer.
            return

    def start(
        self,
        *,
        subagent_id: str,
        tool_use_id: str,
        goal: str,
        depth: int,
        model: str = "",
        profile: str = "",
        workdir: str = "",
        goal_text: str = "",
    ) -> None:
        self.record(
            EVENT_START,
            {
                "subagent_id": str(subagent_id),
                "tool_use_id": str(tool_use_id or ""),
                "depth": int(depth),
                "goal_title": safe_goal_title(goal),
                "model": str(model or ""),
                "profile": str(profile or ""),
                "status": STATUS_RUNNING,
                "workdir": workdir,
                "goal_text": goal_text or goal,
            },
        )

    def progress(
        self,
        *,
        subagent_id: str,
        stage: str,
        tool_name: str = "",
        tool_calls_count: int | None = None,
        note: str = "",
    ) -> None:
        payload: dict[str, Any] = {
            "subagent_id": str(subagent_id),
            "stage": str(stage or ""),
        }
        if tool_name:
            payload["tool_name"] = str(tool_name)[:120]
        if tool_calls_count is not None:
            payload["tool_calls_count"] = int(tool_calls_count)
        if note:
            payload["note"] = _WHITESPACE_RE.sub(" ", str(note)).strip()[:_MAX_NOTE_CHARS]
        self.record(EVENT_PROGRESS, payload)

    def finish(
        self,
        *,
        subagent_id: str,
        status: str,
        summary: str = "",
        error: str = "",
        usage: dict[str, Any] | None = None,
        tool_calls_count: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "subagent_id": str(subagent_id),
            "status": str(status or STATUS_OK),
            "summary": clip_summary(summary),
        }
        if error:
            payload["error"] = clip_summary(error, limit=1_000)
        if isinstance(usage, dict) and usage:
            payload["usage"] = usage
        if tool_calls_count is not None:
            payload["tool_calls_count"] = int(tool_calls_count)
        self.record(EVENT_FINISH, payload)

    def request_cancel(self, subagent_id: str) -> bool:
        """Sets the address cancellation file flag (visible to guest via virtiofs)."""
        directory = subagents_cancel_dir(self.artifact_workdir)
        if directory is None or not _SUBAGENT_ID.fullmatch(str(subagent_id)):
            return False
        target = directory / str(subagent_id)
        try:
            atomic_write_file(target, str(int(time.time())), mode=0o600)
        except (OSError, ValueError):
            return False
        return True


def cancel_requested(
    artifact_workdir: str | os.PathLike[str] | None, subagent_id: str
) -> bool:
    """True if the subagent has the cancellation file flag set."""
    directory = subagents_cancel_dir(artifact_workdir)
    if directory is None or not _SUBAGENT_ID.fullmatch(str(subagent_id)):
        return False
    try:
        with open_regular_file(directory / str(subagent_id)):
            return True
    except (OSError, ValueError):
        return False


def _coerce_ts(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def fold_subagent_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapses ``subagent.*`` events into a single entry per run.

    Order: by event time (active events first, then recently completed), within each — by start time. Works only with structured payload fields, no lexical heuristics.
    """
    records: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        kind = str(event.get("kind") or "")
        if not kind.startswith("subagent."):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        subagent_id = str(payload.get("subagent_id") or "")
        if not subagent_id:
            continue
        ts = _coerce_ts(event.get("ts")) or 0
        record = records.get(subagent_id)
        if record is None:
            record = {
                "subagent_id": subagent_id,
                "session_id": str(event.get("session_id") or "") or "",
                "parent_task_id": str(payload.get("parent_task_id") or event.get("task_id") or ""),
                "tool_use_id": "",
                "depth": 0,
                "status": STATUS_RUNNING,
                "goal_title": "",
                "model": "",
                "profile": "",
                "last_stage": "",
                "last_tool": "",
                "tool_calls_count": 0,
                "summary": "",
                "error": "",
                "usage": {},
                "started_at": ts,
                "updated_at": ts,
                "finished_at": None,
                "last_seq": int(event.get("seq") or 0),
            }
            records[subagent_id] = record
            order.append(subagent_id)

        if ts:
            record["updated_at"] = ts
        record["last_seq"] = max(int(record["last_seq"] or 0), int(event.get("seq") or 0))

        if kind == "subagent.start":
            for field in ("agent_session_id", "parent_session_id", "root_session_id", "workdir", "goal_text"):
                if field in payload:
                    record[field] = payload[field]
            record["tool_use_id"] = str(payload.get("tool_use_id") or "")
            record["depth"] = int(payload.get("depth") or 0)
            record["goal_title"] = str(payload.get("goal_title") or "")
            record["model"] = str(payload.get("model") or "")
            record["profile"] = str(payload.get("profile") or "")
            record["status"] = str(payload.get("status") or STATUS_RUNNING)
            record["started_at"] = ts or record["started_at"]
            if not record["parent_task_id"]:
                record["parent_task_id"] = str(payload.get("parent_task_id") or "")
        elif kind == "subagent.progress":
            record["last_stage"] = str(payload.get("stage") or "")
            if record["last_stage"] == "waiting_input":
                record["status"] = "waiting_input"
            elif record["status"] == "waiting_input":
                record["status"] = STATUS_RUNNING
            if payload.get("tool_name"):
                record["last_tool"] = str(payload.get("tool_name") or "")
            if payload.get("tool_calls_count") is not None:
                record["tool_calls_count"] = int(payload.get("tool_calls_count") or 0)
        elif kind == "subagent.finish":
            record["status"] = str(payload.get("status") or STATUS_OK)
            record["summary"] = str(payload.get("summary") or "")
            record["error"] = str(payload.get("error") or "")
            record["usage"] = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            if payload.get("tool_calls_count") is not None:
                record["tool_calls_count"] = int(payload.get("tool_calls_count") or 0)
            record["finished_at"] = ts or record["updated_at"]
        elif kind == "subagent.cancel_requested":
            if record["status"] == STATUS_RUNNING:
                record["last_stage"] = "cancel_requested"

    for record in records.values():
        record["duration_ms"] = max(
            0, int((record["updated_at"] or 0) - (record["started_at"] or 0)) * 1000
        )
    return [records[subagent_id] for subagent_id in order]
